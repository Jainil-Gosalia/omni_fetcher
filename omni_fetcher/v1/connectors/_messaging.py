"""Shared spec for the cloud-messaging stream connector family (v1.15).

The "consume a broker, emit one ``Result`` per message, forever" shape is the
same across message queues (AWS Kinesis, GCP Pub/Sub, RabbitMQ/AMQP); what
differs per broker is the connection, the poll/consume loop, the ack model, and
the resume position. This module holds the genuinely shared part -- and only
that: the fold of one consumed message into the canonical per-item ``Result``.

Each connector owns its URI parsing, credentials, consume loop, and the
descriptive ``source_extra`` (including its resume position, which
``stream_with_restart`` reads to reopen a dropped stream). This mirrors the
Kafka/Redis streaming connectors rather than sharing a base class.
"""

from __future__ import annotations

from typing import Any, Mapping

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.mapping import SequenceCounter, build_node, now_utc, stamp_temporal
from omni_fetcher.v1.result import Result, success

# Advisory semantic ``kind`` for every node a messaging connector emits.
MESSAGE_KIND = "message"


def build_message_result(
    *,
    uri: str,
    namespace: str,
    content: str,
    fields: Mapping[str, Any],
    counter: SequenceCounter,
    text_format: TextFormat = TextFormat.OPAQUE,
) -> Result:
    """
    Fold one consumed broker message into the canonical per-item ``Result``

    Builds a ``kind="message"`` node whose single ``Text`` atom carries the
    decoded payload -- ``OPAQUE`` by default, since a broker payload is arbitrary
    decoded bytes with no asserted surface syntax -- and whose descriptive
    ``fields`` (including the resume position) live in
    ``source_extra[namespace]``. The node is stamped with the per-stream
    sequence and a wall-clock timestamp.

    Parameters
    ----------
        uri:
            The source URI (``source_url``).
        namespace:
            The ``source_extra`` namespace (``"kinesis"`` / ``"pubsub"`` /
            ``"amqp"``).
        content:
            The decoded message payload.
        fields:
            Descriptive fields for ``source_extra[namespace]`` (message id,
            timestamp, and the resume position).
        counter:
            The per-stream sequence counter.
        text_format:
            The payload's canonical ``TextFormat`` (``OPAQUE`` by default).

    Return
    ------
        result:
            A ``Success`` carrying the message node.
    """
    node = build_node(
        kind=MESSAGE_KIND,
        atoms=[Text(content=content, format=text_format)],
        source_url=uri,
        source_namespace=namespace,
        source_fields=dict(fields),
    )
    stamp_temporal(node, sequence=counter.next(), timestamp=now_utc())
    return success(node)
