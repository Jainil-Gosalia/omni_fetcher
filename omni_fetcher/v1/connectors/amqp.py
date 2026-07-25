"""The canonical ``amqp`` connector -- unbounded RabbitMQ/AMQP stream for v1.

Consumes an AMQP 0-9-1 queue (RabbitMQ and compatible brokers) through the v1
contract: each message is one ``Result`` whose tree is a single
``kind="message"`` node carrying one ``Text`` atom (the decoded body) plus facts
in ``source_extra["amqp"]`` (message_id, routing_key, exchange, delivery_tag,
queue). Each message is acknowledged **after** it is yielded (explicit
at-least-once after delivery); an unacked message is requeued by the broker, so a
dropped stream resumes with no URI position.

Configuration travels in the URI: ``amqp://[user[:pass]@]host[:port]/<queue>``
(``amqps://`` for TLS, default ports 5672 / 5671) with ``?vhost=<vhost>``
(default ``/``). Credentials are a per-call ``BasicAuth`` (MCP injects
``OMNI_FETCHER_AMQP_USERNAME`` / ``_PASSWORD``) which overrides any URI userinfo;
absent both, the broker default ``guest``/``guest`` is used.

``aio-pika`` is optional (the ``amqp`` extra): this module imports without it,
``builtin_registry()`` skips the source when it is missing, and direct use
yields a typed ``UNSUPPORTED``. Stream-only: ``fetch()`` is ``UNSUPPORTED``. A
transport failure yields one terminal ``TRANSIENT``. All broker access flows
through the ``_consume`` seam so tests script a fake.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from typing import Any, AsyncGenerator, AsyncIterator, Optional
from urllib.parse import parse_qs, urlsplit

from omni_fetcher.v1.auth import AuthCredential, BasicAuth
from omni_fetcher.v1.connectors._messaging import build_message_result
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter
from omni_fetcher.v1.result import Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

SOURCE_NAMESPACE = "amqp"

# Whether the optional aio-pika client is importable (the ``amqp`` extra).
AMQP_AVAILABLE = importlib.util.find_spec("aio_pika") is not None

_DEFAULT_PORT = 5672
_DEFAULT_TLS_PORT = 5671
_DEFAULT_USER = "guest"


class _AmqpSpec:
    """Parsed ``amqp://`` routing decision."""

    def __init__(
        self,
        host: str,
        port: int,
        queue: str,
        vhost: str,
        tls: bool,
        uri_user: Optional[str],
        uri_password: Optional[str],
    ) -> None:
        self.host = host
        self.port = port
        self.queue = queue
        self.vhost = vhost
        self.tls = tls
        self.uri_user = uri_user
        self.uri_password = uri_password


def _parse_uri(uri: str) -> _AmqpSpec:
    """Parse an ``amqp://[user[:pass]@]host[:port]/queue?vhost=`` URI."""
    parts = urlsplit(uri)
    if parts.scheme not in ("amqp", "amqps"):
        raise ValueError(f"not an amqp:// URI: {uri}")
    host = parts.hostname
    queue = parts.path.lstrip("/")
    if not host or not queue or "/" in queue:
        raise ValueError(f"amqp:// URI must be amqp://host[:port]/queue: {uri}")
    tls = parts.scheme == "amqps"
    port = parts.port or (_DEFAULT_TLS_PORT if tls else _DEFAULT_PORT)
    vhost = parse_qs(parts.query).get("vhost", ["/"])[0]
    return _AmqpSpec(
        host=host,
        port=port,
        queue=queue,
        vhost=vhost,
        tls=tls,
        uri_user=parts.username,
        uri_password=parts.password,
    )


def _resolve_credentials(spec: _AmqpSpec, auth: Optional[AuthCredential]) -> tuple[str, str]:
    """Resolve ``(user, password)``: BasicAuth wins, then URI userinfo, then guest."""
    if isinstance(auth, BasicAuth):
        return auth.username, auth.password
    if spec.uri_user is not None:
        return spec.uri_user, spec.uri_password or ""
    return _DEFAULT_USER, _DEFAULT_USER


class AMQPConnector(BaseFetcher):
    """
    Unbounded RabbitMQ/AMQP connector for the v1 contract
    ===============================================
    Streams queue messages as canonical per-item ``Result``s, acking each after
    it is yielded. ``fetch()`` is a typed ``UNSUPPORTED``. All broker access
    goes through the ``_consume`` seam.
    ===============================================
    NOTE:
        1. Credentials are a per-call ``BasicAuth`` (overriding URI userinfo);
           absent both, the broker default ``guest``/``guest`` is used.
        2. An unacked message is requeued by the broker, so there is no URI
           resume position; a dropped stream simply reopens.
        3. ``aio-pika`` is optional (the ``amqp`` extra); without it the
           connector yields a typed ``UNSUPPORTED``.

    Methods
    -------
        stream:
        fetch:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` names an AMQP queue."""
        return uri.startswith("amqp://") or uri.startswith("amqps://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Consume an AMQP queue and yield one ``Result`` per message, forever

        Parameters
        ----------
            uri:
                The ``amqp://host/queue`` source URI.
            auth:
                Optional per-call ``BasicAuth`` (overrides URI userinfo).
            zoom:
                Accepted for protocol conformance.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del zoom

        if not AMQP_AVAILABLE:
            yield error(
                ErrorKind.UNSUPPORTED,
                message=(
                    "aio-pika is not installed; install the 'amqp' extra "
                    '(pip install "omni-fetcher[amqp]") to consume amqp://'
                ),
                locator=uri,
            )
            return

        try:
            spec = _parse_uri(uri)
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        counter = SequenceCounter()
        consumer = self._consume(spec, auth)
        try:
            async for message in consumer:
                yield build_message_result(
                    uri=uri,
                    namespace=SOURCE_NAMESPACE,
                    content=message["content"],
                    fields=message["fields"],
                    counter=counter,
                )
        except Exception as exc:  # noqa: BLE001 - boundary: returned as a typed Error
            yield from_exception(exc, kind=ErrorKind.TRANSIENT, locator=uri)
        finally:
            await consumer.aclose()

    async def fetch(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> Result:
        """Refuse collection of an unbounded source (typed, immediate)."""
        del auth, zoom
        return error(
            kind=ErrorKind.UNSUPPORTED,
            message=(
                "amqp:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _consume(
        self, spec: _AmqpSpec, auth: Optional[AuthCredential]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Consume the queue and yield raw dicts, acking each (broker seam).

        Production connects with aio-pika and iterates the queue, acking each
        message after it is yielded; tests replace this with a scripted async
        generator.
        """
        import aio_pika

        user, password = _resolve_credentials(spec, auth)
        connection = await aio_pika.connect_robust(
            host=spec.host,
            port=spec.port,
            login=user,
            password=password,
            virtualhost=spec.vhost,
            ssl=spec.tls,
        )
        try:
            channel = await connection.channel()
            queue = await channel.get_queue(spec.queue)
            async with queue.iterator() as iterator:
                async for message in iterator:
                    yield self._to_message(message, spec.queue)
                    await message.ack()
        finally:
            await connection.close()

    @staticmethod
    def _to_message(message: Any, queue: str) -> dict[str, Any]:
        """Map one aio-pika message onto a raw message dict."""
        body = getattr(message, "body", b"")
        content = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        timestamp_value = getattr(message, "timestamp", None)
        timestamp = timestamp_value.isoformat() if isinstance(timestamp_value, datetime) else None
        return {
            "content": content,
            "fields": {
                "message_id": getattr(message, "message_id", None),
                "routing_key": getattr(message, "routing_key", None),
                "exchange": getattr(message, "exchange", None),
                "delivery_tag": getattr(message, "delivery_tag", None),
                "queue": queue,
                "timestamp": timestamp,
            },
        }
