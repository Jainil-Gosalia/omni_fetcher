"""The typed error taxonomy for the OmniFetcher v1 contract.

Expected failures are returned as typed values inside a ``Result`` (see
``result.py``), never raised. Raised exceptions signal programmer error
only. ``ErrorKind`` is the closed taxonomy a consumer branches on.
"""

from __future__ import annotations

from enum import Enum


class ErrorKind(str, Enum):
    """
    The typed error taxonomy
    ===============================================
    The closed set of failure kinds an OmniFetcher call can return. A
    consumer branches on these instead of defensively parsing strings.
    ``unsupported`` is deliberately distinct from ``not_found`` and
    ``transient``.
    ===============================================
    NOTE:
        1. This set evolves additively; existing members never change
           meaning.

    Attributes
    ----------
        AUTH_FAILED:
            Authentication failed (bad/expired/missing credential).
        PERMISSION_DENIED:
            Authenticated but lacking sufficient scope/permission.
        NOT_FOUND:
            The requested resource does not exist.
        UNSUPPORTED:
            The source/sub-feature is recognised but not supported.
        RATE_LIMITED:
            The source rate-limited the request.
        TRANSIENT:
            A transient/retryable failure (network blip, 5xx, timeout).
        PARSE_ERROR:
            The source content could not be parsed/decoded.
        INVALID_INPUT:
            The caller supplied invalid input (bad URI, bad zoom, ...).
    """

    AUTH_FAILED = "auth_failed"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    PARSE_ERROR = "parse_error"
    INVALID_INPUT = "invalid_input"


# Built-in exception types whose meaning maps cleanly onto a taxonomy kind.
# Connectors lean on richer source-specific exceptions and should usually pass
# an explicit ``kind`` to ``classify_exception``; this table is the sane
# default used when no explicit kind is supplied. Order is irrelevant -- the
# match is by exact-or-subclass membership, most-specific first.
_DEFAULT_EXCEPTION_KINDS: tuple[tuple[type[BaseException], ErrorKind], ...] = (
    (PermissionError, ErrorKind.PERMISSION_DENIED),
    (FileNotFoundError, ErrorKind.NOT_FOUND),
    (NotImplementedError, ErrorKind.UNSUPPORTED),
    (TimeoutError, ErrorKind.TRANSIENT),
    (ConnectionError, ErrorKind.TRANSIENT),
    (UnicodeError, ErrorKind.PARSE_ERROR),
    (ValueError, ErrorKind.INVALID_INPUT),
    (TypeError, ErrorKind.INVALID_INPUT),
)


def classify_exception(
    exc: BaseException,
    *,
    default: ErrorKind = ErrorKind.TRANSIENT,
) -> ErrorKind:
    """
    Map a caught exception onto a taxonomy kind

    Best-effort classification of a built-in exception into an
    :class:`ErrorKind`. Connectors that know the failure's meaning should
    pass an explicit kind to :func:`omni_fetcher.v1.result.from_exception`
    instead of relying on this; this exists so a broad catch can still
    produce a sensibly-typed error rather than an opaque one.

    Parameters
    ----------
        exc:
            The caught exception to classify.
        default:
            The kind returned when ``exc`` matches no known mapping. Defaults
            to ``TRANSIENT`` -- an unknown failure is assumed retryable rather
            than terminal.

    Return
    ------
        kind:
            The taxonomy kind ``exc`` maps onto, or ``default``.
    """
    for exc_type, kind in _DEFAULT_EXCEPTION_KINDS:
        if isinstance(exc, exc_type):
            return kind
    return default


def describe_exception(exc: BaseException) -> str:
    """
    Render an exception (and its cause chain) as a non-opaque string

    Produces a stable, human-readable description that preserves the
    exception type, its message, and any chained cause (``raise ... from``)
    or context. This is what error wrapping uses so the original cause is
    never flattened to a bare opaque string.

    Parameters
    ----------
        exc:
            The exception to describe.

    Return
    ------
        description:
            A string of the form ``"TypeName: message"`` with any chained
            cause appended as ``" <- caused by: ..."``.
    """
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current)
        rendered = type(current).__name__
        if text:
            rendered = f"{rendered}: {text}"
        parts.append(rendered)
        # Honour an explicit ``raise ... from cause`` first; fall back to the
        # implicit context only when the chain was not suppressed.
        nxt = current.__cause__
        if nxt is None and not current.__suppress_context__:
            nxt = current.__context__
        current = nxt
    return " <- caused by: ".join(parts)
