"""The typed ``Result`` envelope for the OmniFetcher v1 contract.

Boundary calls (``fetch`` / ``stream``) return a ``Result``: a tagged union
over three states.

- ``success(tree)``  -- a complete canonical composition tree.
- ``partial(tree, gaps)`` -- the tree that could be built, plus a typed list
  of ``Gap``s describing what failed or was skipped.
- ``error(kind)`` -- an expected failure, carrying a typed ``ErrorKind``.

Expected failures are returned, not raised. A degraded fetch is ``partial``
or ``error``, never a ``success`` with silently missing content.

Branch on a result via its ``state`` discriminator, the ``is_success`` /
``is_partial`` / ``is_error`` properties, or ``match(...)``.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Callable, Literal, Optional, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from omni_fetcher.v1.errors import (
    ErrorKind,
    classify_exception,
    describe_exception,
)
from omni_fetcher.v1.node import CompositionNode

T = TypeVar("T")


class ResultState(str, Enum):
    """
    The three states of a ``Result``
    ===============================================
    The discriminator tag for the ``Result`` union.
    ===============================================

    Attributes
    ----------
        SUCCESS:
            A complete tree was produced.
        PARTIAL:
            A partial tree was produced, with typed gaps.
        ERROR:
            An expected failure occurred.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


class Gap(BaseModel):
    """
    A typed description of missing or skipped content
    ===============================================
    Records one piece of content that could not be produced, so a
    ``partial`` result is explicit about what is missing rather than
    silently incomplete.
    ===============================================

    Attributes
    ----------
        kind:
            The typed reason the content is missing.
        locator:
            Optional pointer to where the gap occurs (e.g. a sub-URI, a
            child path, a node id).
        detail:
            Optional human-readable detail about the gap.
    """

    model_config = ConfigDict(frozen=True)

    kind: ErrorKind
    locator: Optional[str] = None
    detail: Optional[str] = None


class Success(BaseModel):
    """
    A successful result
    ===============================================
    Carries the complete canonical composition tree.
    ===============================================

    Attributes
    ----------
        state:
            Always ``ResultState.SUCCESS``.
        tree:
            The complete canonical composition tree.
    """

    model_config = ConfigDict(frozen=True)

    state: Literal[ResultState.SUCCESS] = ResultState.SUCCESS
    tree: CompositionNode


class Partial(BaseModel):
    """
    A partial result
    ===============================================
    Carries the tree that could be built plus a typed list of gaps
    describing what failed or was skipped. Never used to hide missing
    content as a success.
    ===============================================

    Attributes
    ----------
        state:
            Always ``ResultState.PARTIAL``.
        tree:
            The partial canonical composition tree.
        gaps:
            Typed list of what failed or was skipped (non-empty by
            convention).
    """

    model_config = ConfigDict(frozen=True)

    state: Literal[ResultState.PARTIAL] = ResultState.PARTIAL
    tree: CompositionNode
    gaps: list[Gap] = Field(default_factory=list)


class Error(BaseModel):
    """
    An error result
    ===============================================
    Carries a typed ``ErrorKind``. Expected failures are returned this way,
    never raised. Any wrapped internal cause is preserved by the producer
    (chained), not flattened into this value.
    ===============================================

    Attributes
    ----------
        state:
            Always ``ResultState.ERROR``.
        kind:
            The typed error kind from the taxonomy.
        message:
            Optional human-readable, non-load-bearing detail.
        locator:
            Optional pointer to the resource that failed.
    """

    model_config = ConfigDict(frozen=True)

    state: Literal[ResultState.ERROR] = ResultState.ERROR
    kind: ErrorKind
    message: Optional[str] = None
    locator: Optional[str] = None


# The tagged union returned by boundary calls. Discriminated on ``state`` so
# Pydantic validates each arm against exactly the right model (and a caller
# can rebuild a ``Result`` from JSON/dict without guessing the variant).
Result = Annotated[
    Union[Success, Partial, Error],
    Field(discriminator="state"),
]

# A reusable validator for the discriminated union. ``Result`` is a typing
# construct, not a class, so this is how a consumer parses untyped data
# (e.g. an MCP payload) back into the correct concrete arm.
ResultAdapter: TypeAdapter[Union[Success, Partial, Error]] = TypeAdapter(Result)


def success(tree: CompositionNode) -> Success:
    """
    Construct a success result

    Parameters
    ----------
        tree:
            The complete canonical composition tree.

    Return
    ------
        result:
            A ``Success`` wrapping ``tree``.
    """
    return Success(tree=tree)


def partial(tree: CompositionNode, gaps: list[Gap]) -> Partial:
    """
    Construct a partial result

    Parameters
    ----------
        tree:
            The partial canonical composition tree.
        gaps:
            Typed list of what failed or was skipped.

    Return
    ------
        result:
            A ``Partial`` wrapping ``tree`` and ``gaps``.
    """
    return Partial(tree=tree, gaps=list(gaps))


def error(
    kind: ErrorKind,
    message: Optional[str] = None,
    locator: Optional[str] = None,
) -> Error:
    """
    Construct an error result

    Parameters
    ----------
        kind:
            The typed error kind from the taxonomy.
        message:
            Optional human-readable detail.
        locator:
            Optional pointer to the resource that failed.

    Return
    ------
        result:
            An ``Error`` carrying ``kind``.
    """
    return Error(kind=kind, message=message, locator=locator)


def gap(
    kind: ErrorKind,
    locator: Optional[str] = None,
    detail: Optional[str] = None,
) -> Gap:
    """
    Construct a typed gap

    A ``Gap`` records one piece of content that could not be produced, for
    use inside a ``partial`` result. This is a value, not an exception.

    Parameters
    ----------
        kind:
            The typed reason the content is missing.
        locator:
            Optional pointer to where the gap occurs.
        detail:
            Optional human-readable detail about the gap.

    Return
    ------
        gap:
            A ``Gap`` carrying ``kind``.
    """
    return Gap(kind=kind, locator=locator, detail=detail)


def unsupported(
    message: Optional[str] = None,
    locator: Optional[str] = None,
) -> Error:
    """
    Construct an ``unsupported`` error result

    Convenience for the ``ErrorKind.UNSUPPORTED`` signal a connector returns
    when it recognises a source/sub-feature but cannot represent it. Distinct
    from ``not_found`` (the resource does not exist) and ``transient`` (a
    retryable blip). Use :func:`unsupported_gap` to express the same signal
    as a gap inside a ``partial`` tree.

    Parameters
    ----------
        message:
            Optional human-readable detail about what is unsupported.
        locator:
            Optional pointer to the unsupported resource/sub-feature.

    Return
    ------
        result:
            An ``Error`` carrying ``ErrorKind.UNSUPPORTED``.
    """
    return Error(
        kind=ErrorKind.UNSUPPORTED,
        message=message,
        locator=locator,
    )


def unsupported_gap(
    locator: Optional[str] = None,
    detail: Optional[str] = None,
) -> Gap:
    """
    Construct an ``unsupported`` gap

    The ``unsupported`` signal expressed as a ``Gap`` so a connector can
    return a ``partial`` tree that covers what it can and is explicit about
    the sub-feature it cannot represent -- never a ``success`` with silently
    missing content.

    Parameters
    ----------
        locator:
            Optional pointer to the unsupported sub-feature.
        detail:
            Optional human-readable detail about what is unsupported.

    Return
    ------
        gap:
            A ``Gap`` carrying ``ErrorKind.UNSUPPORTED``.
    """
    return Gap(
        kind=ErrorKind.UNSUPPORTED,
        locator=locator,
        detail=detail,
    )


def from_exception(
    exc: BaseException,
    *,
    kind: Optional[ErrorKind] = None,
    message: Optional[str] = None,
    locator: Optional[str] = None,
) -> Error:
    """
    Wrap a caught exception into an ``Error``, preserving its cause

    Converts an exception caught at the boundary into a returned ``Error``
    value rather than re-raising. The original exception type, message, and
    any chained cause are rendered into the error ``message`` (via
    :func:`omni_fetcher.v1.errors.describe_exception`) so the cause is
    preserved, never flattened to a bare opaque string.

    NOTE:
        1. This itself does not raise -- it returns a value.
        2. If ``kind`` is not supplied it is inferred from ``exc`` via
           :func:`omni_fetcher.v1.errors.classify_exception`.

    Parameters
    ----------
        exc:
            The caught exception to wrap.
        kind:
            Optional explicit taxonomy kind. Inferred from ``exc`` when
            omitted.
        message:
            Optional caller-supplied prefix prepended to the rendered cause.
        locator:
            Optional pointer to the resource that failed.

    Return
    ------
        result:
            An ``Error`` whose ``message`` preserves ``exc``'s type, message,
            and cause chain.
    """
    resolved_kind = kind if kind is not None else classify_exception(exc)
    cause = describe_exception(exc)
    full_message = f"{message}: {cause}" if message else cause
    return Error(
        kind=resolved_kind,
        message=full_message,
        locator=locator,
    )


def gap_from_exception(
    exc: BaseException,
    *,
    kind: Optional[ErrorKind] = None,
    locator: Optional[str] = None,
) -> Gap:
    """
    Wrap a caught exception into a ``Gap``, preserving its cause

    The ``Gap`` analogue of :func:`from_exception`, for recording a wrapped
    failure inside a ``partial`` result. The original type, message, and
    cause chain are preserved in the gap ``detail``.

    Parameters
    ----------
        exc:
            The caught exception to wrap.
        kind:
            Optional explicit taxonomy kind. Inferred from ``exc`` when
            omitted.
        locator:
            Optional pointer to where the gap occurs.

    Return
    ------
        gap:
            A ``Gap`` whose ``detail`` preserves ``exc``'s cause chain.
    """
    resolved_kind = kind if kind is not None else classify_exception(exc)
    return Gap(
        kind=resolved_kind,
        locator=locator,
        detail=describe_exception(exc),
    )


def is_success(result: Result) -> bool:
    """
    Report whether a result is a success

    Parameters
    ----------
        result:
            The result to inspect.

    Return
    ------
        is_success:
            ``True`` if ``result`` is a ``Success``.
    """
    return result.state is ResultState.SUCCESS


def is_partial(result: Result) -> bool:
    """
    Report whether a result is partial

    Parameters
    ----------
        result:
            The result to inspect.

    Return
    ------
        is_partial:
            ``True`` if ``result`` is a ``Partial``.
    """
    return result.state is ResultState.PARTIAL


def is_error(result: Result) -> bool:
    """
    Report whether a result is an error

    Parameters
    ----------
        result:
            The result to inspect.

    Return
    ------
        is_error:
            ``True`` if ``result`` is an ``Error``.
    """
    return result.state is ResultState.ERROR


def match(
    result: Result,
    *,
    on_success: Callable[[Success], T],
    on_partial: Callable[[Partial], T],
    on_error: Callable[[Error], T],
) -> T:
    """
    Branch on a result's state and return a value

    Parameters
    ----------
        result:
            The result to branch on.
        on_success:
            Handler invoked with the ``Success`` when ``result`` succeeded.
        on_partial:
            Handler invoked with the ``Partial`` when ``result`` is partial.
        on_error:
            Handler invoked with the ``Error`` when ``result`` errored.

    Return
    ------
        value:
            The value returned by the matched handler.
    """
    if isinstance(result, Success):
        return on_success(result)
    if isinstance(result, Partial):
        return on_partial(result)
    return on_error(result)
