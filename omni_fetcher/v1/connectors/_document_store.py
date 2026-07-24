"""Shared spec for the NoSQL document-store connector family (v1.16).

The "read a set of JSON documents, return one container of ``json_document``
children" shape is the same across document stores (MongoDB, DynamoDB); what
differs per store is the driver, the query surface, and the auth model. This
module holds the genuinely shared part -- and only that: the fold of fetched
documents into the canonical container ``Result``, mirroring the shape the
``elasticsearch`` connector established (a ``json_document`` node carries one
``Text`` atom, ``format=CODE``, the document serialised as JSON -- there is no
``JSONData`` atom in v1's closed vocabulary).

Each connector owns its URI parsing, credentials, query, and error mapping. This
is a spec, not a base class doing the work.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Gap, Result, gap, partial, success

# Advisory semantic ``kind`` values for the nodes this family emits.
DOCUMENTS_KIND = "documents"
DOCUMENT_KIND = "json_document"

# Document-cap defaults: a query returns at most ``DEFAULT_DOC_CAP`` documents
# unless ``?limit=`` raises it, never above ``MAX_DOC_CAP``.
DEFAULT_DOC_CAP = 1000
MAX_DOC_CAP = 100_000


def resolve_doc_cap(limit_param: Optional[str]) -> int:
    """
    Resolve the effective document cap from an optional ``?limit=`` value

    Returns :data:`DEFAULT_DOC_CAP` when unset, else the parsed value clamped to
    ``[1, MAX_DOC_CAP]``. Raises ``ValueError`` on a non-integer.

    Parameters
    ----------
        limit_param:
            The raw ``?limit=`` string, or ``None``.

    Return
    ------
        cap:
            The effective maximum document count.
    """
    if limit_param is None:
        return DEFAULT_DOC_CAP
    try:
        value = int(limit_param)
    except ValueError as exc:
        raise ValueError(f"limit must be an integer: {limit_param!r}") from exc
    if value < 1:
        raise ValueError(f"limit must be >= 1: {value}")
    return min(value, MAX_DOC_CAP)


def build_document_node(
    uri: str,
    namespace: str,
    document: Any,
    fields: Mapping[str, Any],
) -> CompositionNode:
    """
    Fold one document into a canonical ``json_document`` node

    The document is serialised to JSON as one ``Text`` atom (``format=CODE``);
    per-document facts (id, keys) live in ``source_extra[namespace]``.

    Parameters
    ----------
        uri:
            The source URI (``source_url``).
        namespace:
            The ``source_extra`` namespace.
        document:
            The document (any JSON-serialisable value; non-JSON scalars are
            coerced by ``default=str``).
        fields:
            Per-document descriptive fields.

    Return
    ------
        node:
            The canonical document node.
    """
    content = json.dumps(document, ensure_ascii=False, indent=2, default=str)
    return build_node(
        kind=DOCUMENT_KIND,
        atoms=[Text(content=content, format=TextFormat.CODE)],
        source_url=uri,
        source_namespace=namespace,
        source_fields=dict(fields),
    )


def build_documents_result(
    uri: str,
    namespace: str,
    documents: Sequence[tuple[Any, Mapping[str, Any]]],
    *,
    doc_cap: int,
    container_fields: Optional[Mapping[str, Any]] = None,
) -> Result:
    """
    Fold fetched documents into one ``kind="documents"`` container ``Result``

    ``documents`` is a sequence of ``(document, per_doc_fields)`` pairs, up to
    ``doc_cap + 1``: an over-cap result is truncated to ``doc_cap`` and returned
    as a ``Partial`` whose ``Gap`` names the cap, so truncation is never silent.

    Parameters
    ----------
        uri:
            The source URI.
        namespace:
            The ``source_extra`` namespace.
        documents:
            The fetched ``(document, fields)`` pairs, up to ``doc_cap + 1``.
        doc_cap:
            The applied document cap.
        container_fields:
            Descriptive fields merged into the container's
            ``source_extra[namespace]``.

    Return
    ------
        result:
            A ``Success`` (within cap) or ``Partial`` (truncated) container.
    """
    truncated = len(documents) > doc_cap
    kept = documents[:doc_cap] if truncated else documents
    children = [build_document_node(uri, namespace, doc, fields) for doc, fields in kept]

    fields: dict[str, Any] = {"document_count": len(children), "truncated": truncated}
    if container_fields:
        fields.update(dict(container_fields))

    container = build_node(
        kind=DOCUMENTS_KIND,
        children=children,
        source_url=uri,
        source_namespace=namespace,
        source_fields=fields,
    )
    if truncated:
        truncation: Gap = gap(
            kind=ErrorKind.UNSUPPORTED,
            locator=uri,
            detail=(
                f"result truncated to the {doc_cap}-document cap; more documents exist. "
                "Raise ?limit= (up to the hard ceiling) or narrow the query"
            ),
        )
        return partial(container, [truncation])
    return success(container)
