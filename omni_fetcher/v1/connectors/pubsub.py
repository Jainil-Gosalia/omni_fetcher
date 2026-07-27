"""The canonical ``pubsub`` connector -- unbounded GCP Pub/Sub stream for v1.

Consumes a Google Cloud Pub/Sub subscription through the v1 contract: each
message is one ``Result`` whose tree is a single ``kind="message"`` node
carrying one ``Text`` atom (the decoded payload) plus facts in
``source_extra["pubsub"]`` (message_id, publish_time, subscription). Each
message is acknowledged **after** it is yielded (explicit at-least-once after
delivery); an unacked message is redelivered by Pub/Sub, so a dropped stream
resumes with no URI position -- ``stream_with_restart`` simply reopens the
subscription.

Configuration travels in the URI: ``pubsub://<project>/<subscription>``.
Credentials are supplied per call as an ``OAuth2Auth`` access token (per
PHILOSOPHY §7 a service-account key is a host-side token exchange), so the MCP
server injects ``OMNI_FETCHER_PUBSUB_ACCESS_TOKEN``; a call with no
``OAuth2Auth`` is an ``AUTH_FAILED`` error.

``google-cloud-pubsub`` is optional (the ``pubsub`` extra): this module imports
without it, ``builtin_registry()`` skips the source when it is missing, and
direct use yields a typed ``UNSUPPORTED``. Stream-only: ``fetch()`` is
``UNSUPPORTED``. A transport failure yields one terminal ``TRANSIENT``. All
broker access flows through the ``_consume`` seam so tests script a fake.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncGenerator, AsyncIterator, Optional

from omni_fetcher.v1.auth import AuthCredential, OAuth2Auth
from omni_fetcher.v1.connectors._messaging import build_message_result
from omni_fetcher.v1.connectors._optional import module_available
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter
from omni_fetcher.v1.result import Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

SOURCE_NAMESPACE = "pubsub"

# Whether the optional google-cloud-pubsub client is importable (``pubsub`` extra).
PUBSUB_AVAILABLE = module_available("google.cloud.pubsub_v1")

_SCHEME = "pubsub://"
_MAX_MESSAGES = 10


class _PubSubSpec:
    """Parsed ``pubsub://`` routing decision (project + subscription)."""

    def __init__(self, project: str, subscription: str) -> None:
        self.project = project
        self.subscription = subscription

    @property
    def path(self) -> str:
        """The fully-qualified subscription path."""
        return f"projects/{self.project}/subscriptions/{self.subscription}"


def _parse_uri(uri: str) -> _PubSubSpec:
    """Parse a ``pubsub://project/subscription`` URI, raising ``ValueError``."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a pubsub:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, _query = remainder.partition("?")
    project, _, subscription = location.partition("/")
    if not project or not subscription or "/" in subscription:
        raise ValueError(f"pubsub:// URI must be pubsub://project/subscription: {uri}")
    return _PubSubSpec(project=project, subscription=subscription)


class PubSubConnector(BaseFetcher):
    """
    Unbounded GCP Pub/Sub connector for the v1 contract
    ===============================================
    Streams messages as canonical per-item ``Result``s, acking each after it is
    yielded. ``fetch()`` is a typed ``UNSUPPORTED``. All broker access goes
    through the ``_consume`` seam.
    ===============================================
    NOTE:
        1. Credentials are a per-call ``OAuth2Auth``; a call without one is
           ``AUTH_FAILED``.
        2. An unacked message is redelivered by Pub/Sub, so there is no URI
           resume position; a dropped stream simply reopens.
        3. ``google-cloud-pubsub`` is optional (the ``pubsub`` extra); without it
           the connector yields a typed ``UNSUPPORTED``.

    Methods
    -------
        stream:
        fetch:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` names a Pub/Sub subscription."""
        return uri.startswith(_SCHEME)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Consume a Pub/Sub subscription and yield one ``Result`` per message

        Parameters
        ----------
            uri:
                The ``pubsub://project/subscription`` source URI.
            auth:
                The per-call ``OAuth2Auth`` credential.
            zoom:
                Accepted for protocol conformance.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del zoom

        if not PUBSUB_AVAILABLE:
            yield error(
                ErrorKind.UNSUPPORTED,
                message=(
                    "google-cloud-pubsub is not installed; install the 'pubsub' extra "
                    '(pip install "omni-fetcher[pubsub]") to consume pubsub://'
                ),
                locator=uri,
            )
            return

        try:
            spec = _parse_uri(uri)
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return
        if not isinstance(auth, OAuth2Auth):
            yield error(
                ErrorKind.AUTH_FAILED,
                message="pubsub requires a per-call OAuth2Auth access token",
                locator=uri,
            )
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
                "pubsub:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _consume(
        self, spec: _PubSubSpec, auth: OAuth2Auth
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Pull Pub/Sub messages and yield raw dicts, acking each (broker seam).

        Production builds a synchronous ``SubscriberClient`` from the per-call
        token and pulls on a worker thread, acking after each message; tests
        replace this with a scripted async generator.
        """
        subscriber = self._client(auth.access_token)
        path = spec.path
        try:
            while True:
                response = await asyncio.to_thread(
                    subscriber.pull,
                    request={"subscription": path, "max_messages": _MAX_MESSAGES},
                )
                received = list(getattr(response, "received_messages", []))
                if not received:
                    continue
                for received_message in received:
                    yield self._to_message(received_message, spec.subscription)
                    await asyncio.to_thread(
                        subscriber.acknowledge,
                        request={"subscription": path, "ack_ids": [received_message.ack_id]},
                    )
        finally:
            close = getattr(subscriber, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    @staticmethod
    def _client(access_token: str) -> Any:
        """Build a synchronous Pub/Sub ``SubscriberClient`` from an access token."""
        from google.cloud import pubsub_v1  # type: ignore[attr-defined]
        from google.oauth2.credentials import Credentials

        credentials = Credentials(token=access_token)
        return pubsub_v1.SubscriberClient(credentials=credentials)

    @staticmethod
    def _to_message(received_message: Any, subscription: str) -> dict[str, Any]:
        """Map one received Pub/Sub message onto a raw message dict."""
        message = received_message.message
        data = getattr(message, "data", b"")
        content = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        publish_time = getattr(message, "publish_time", None)
        timestamp = publish_time.isoformat() if isinstance(publish_time, datetime) else None
        return {
            "content": content,
            "fields": {
                "message_id": getattr(message, "message_id", None),
                "subscription": subscription,
                "publish_time": timestamp,
            },
        }
