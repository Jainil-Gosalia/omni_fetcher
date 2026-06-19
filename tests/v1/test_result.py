"""External-behaviour tests for the v1 Result envelope + error taxonomy.

These tests exercise only the public surface of ``omni_fetcher.v1.result``
and ``omni_fetcher.v1.errors``: the three result states construct and are
discriminable, every taxonomy kind exists (and ``unsupported`` is distinct
from ``not_found`` / ``transient``), expected failures are *returned* rather
than raised, wrapping a caught exception preserves its cause, and
``match`` / the predicates dispatch correctly.
"""

from __future__ import annotations

import pytest

from omni_fetcher.v1.atoms import Text
from omni_fetcher.v1.errors import (
    ErrorKind,
    classify_exception,
    describe_exception,
)
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Error,
    Gap,
    Partial,
    ResultAdapter,
    ResultState,
    Success,
    error,
    from_exception,
    gap,
    gap_from_exception,
    is_error,
    is_partial,
    is_success,
    match,
    partial,
    success,
    unsupported,
    unsupported_gap,
)


def _tree() -> CompositionNode:
    """Build a small valid composition tree for use as a fixture."""
    return CompositionNode(children=[Text(content="hello")])


# ---------------------------------------------------------------------------
# Each state constructs and is discriminable
# ---------------------------------------------------------------------------


class TestStatesConstruct:
    """Every result state builds and reports the right discriminator."""

    def test_success_constructs(self):
        result = success(_tree())
        assert isinstance(result, Success)
        assert result.state is ResultState.SUCCESS
        assert result.tree.children

    def test_partial_constructs(self):
        gaps = [gap(ErrorKind.NOT_FOUND, locator="child/2")]
        result = partial(_tree(), gaps)
        assert isinstance(result, Partial)
        assert result.state is ResultState.PARTIAL
        assert result.tree.children
        assert result.gaps == gaps

    def test_error_constructs(self):
        result = error(ErrorKind.AUTH_FAILED, message="bad token")
        assert isinstance(result, Error)
        assert result.state is ResultState.ERROR
        assert result.kind is ErrorKind.AUTH_FAILED
        assert result.message == "bad token"

    def test_states_are_mutually_discriminable(self):
        s = success(_tree())
        p = partial(_tree(), [gap(ErrorKind.TRANSIENT)])
        e = error(ErrorKind.NOT_FOUND)
        states = {s.state, p.state, e.state}
        assert states == {
            ResultState.SUCCESS,
            ResultState.PARTIAL,
            ResultState.ERROR,
        }

    def test_discriminated_union_round_trips_via_adapter(self):
        # A consumer (e.g. an MCP host) parses untyped data back into the
        # correct concrete arm purely from the ``state`` discriminator.
        for original in (
            success(_tree()),
            partial(_tree(), [gap(ErrorKind.UNSUPPORTED, locator="x")]),
            error(ErrorKind.RATE_LIMITED, message="slow down"),
        ):
            dumped = original.model_dump()
            parsed = ResultAdapter.validate_python(dumped)
            assert type(parsed) is type(original)
            assert parsed.state is original.state

    def test_results_are_frozen(self):
        result = error(ErrorKind.NOT_FOUND)
        with pytest.raises(Exception):
            result.kind = ErrorKind.TRANSIENT


# ---------------------------------------------------------------------------
# Error taxonomy completeness + unsupported distinctness
# ---------------------------------------------------------------------------


class TestTaxonomy:
    """The closed taxonomy is complete and ``unsupported`` is distinct."""

    def test_all_expected_kinds_exist(self):
        expected = {
            "AUTH_FAILED",
            "PERMISSION_DENIED",
            "NOT_FOUND",
            "UNSUPPORTED",
            "RATE_LIMITED",
            "TRANSIENT",
            "PARSE_ERROR",
            "INVALID_INPUT",
        }
        assert {member.name for member in ErrorKind} == expected

    def test_kind_string_values_are_stable(self):
        assert ErrorKind.UNSUPPORTED.value == "unsupported"
        assert ErrorKind.NOT_FOUND.value == "not_found"
        assert ErrorKind.TRANSIENT.value == "transient"

    def test_unsupported_distinct_from_not_found_and_transient(self):
        assert ErrorKind.UNSUPPORTED is not ErrorKind.NOT_FOUND
        assert ErrorKind.UNSUPPORTED is not ErrorKind.TRANSIENT
        assert (
            len(
                {
                    ErrorKind.UNSUPPORTED,
                    ErrorKind.NOT_FOUND,
                    ErrorKind.TRANSIENT,
                }
            )
            == 3
        )


# ---------------------------------------------------------------------------
# No-raise guarantee: constructors return values rather than raising
# ---------------------------------------------------------------------------


class TestNoRaiseGuarantee:
    """Expected failures are returned, not raised."""

    def test_every_kind_constructs_an_error_value(self):
        for kind in ErrorKind:
            result = error(kind)
            assert isinstance(result, Error)
            assert result.kind is kind

    def test_error_constructor_does_not_raise(self):
        # Building a "failure" produces a value; it must not raise.
        result = error(ErrorKind.AUTH_FAILED)
        assert is_error(result)

    def test_from_exception_returns_value_not_raises(self):
        caught = ValueError("nope")
        result = from_exception(caught)
        assert isinstance(result, Error)
        # The function returned normally instead of propagating the error.
        assert is_error(result)

    def test_partial_with_empty_gaps_is_constructible(self):
        # The constructor never raises; an empty gap list is permitted at the
        # type level even though connectors should supply at least one.
        result = partial(_tree(), [])
        assert isinstance(result, Partial)
        assert result.gaps == []


# ---------------------------------------------------------------------------
# Cause preservation when wrapping an exception
# ---------------------------------------------------------------------------


class TestCausePreservation:
    """Wrapping a caught exception preserves type, message, and chain."""

    def test_from_exception_preserves_type_and_message(self):
        result = from_exception(FileNotFoundError("missing.txt"))
        assert "FileNotFoundError" in result.message
        assert "missing.txt" in result.message

    def test_from_exception_infers_kind_from_exception(self):
        assert from_exception(FileNotFoundError()).kind is ErrorKind.NOT_FOUND
        assert (
            from_exception(PermissionError()).kind
            is ErrorKind.PERMISSION_DENIED
        )
        assert (
            from_exception(NotImplementedError()).kind
            is ErrorKind.UNSUPPORTED
        )
        assert from_exception(ValueError()).kind is ErrorKind.INVALID_INPUT

    def test_from_exception_explicit_kind_overrides_inference(self):
        result = from_exception(
            ValueError("x"), kind=ErrorKind.PARSE_ERROR
        )
        assert result.kind is ErrorKind.PARSE_ERROR

    def test_from_exception_preserves_chained_cause(self):
        try:
            try:
                raise KeyError("inner-key")
            except KeyError as inner:
                raise RuntimeError("outer-fail") from inner
        except RuntimeError as exc:
            result = from_exception(exc)
        # Both the outer failure and the chained inner cause survive --
        # nothing is flattened to a bare opaque string.
        assert "RuntimeError" in result.message
        assert "outer-fail" in result.message
        assert "KeyError" in result.message
        assert "inner-key" in result.message
        assert "caused by" in result.message

    def test_from_exception_prefix_message_is_prepended(self):
        result = from_exception(
            ValueError("boom"), message="while parsing page 3"
        )
        assert result.message.startswith("while parsing page 3")
        assert "ValueError" in result.message
        assert "boom" in result.message

    def test_describe_exception_handles_implicit_context(self):
        try:
            try:
                raise ValueError("first")
            except ValueError:
                raise TypeError("second")
        except TypeError as exc:
            described = describe_exception(exc)
        assert "TypeError" in described
        assert "ValueError" in described

    def test_classify_exception_default_is_transient(self):
        class Weird(Exception):
            pass

        assert classify_exception(Weird()) is ErrorKind.TRANSIENT

    def test_classify_exception_respects_custom_default(self):
        class Weird(Exception):
            pass

        assert (
            classify_exception(Weird(), default=ErrorKind.PARSE_ERROR)
            is ErrorKind.PARSE_ERROR
        )

    def test_gap_from_exception_preserves_cause_in_detail(self):
        try:
            raise ValueError("bad row")
        except ValueError as exc:
            g = gap_from_exception(exc, locator="row/7")
        assert isinstance(g, Gap)
        assert g.locator == "row/7"
        assert "ValueError" in g.detail
        assert "bad row" in g.detail
        assert g.kind is ErrorKind.INVALID_INPUT


# ---------------------------------------------------------------------------
# unsupported usable as both standalone Error and as a Gap in Partial
# ---------------------------------------------------------------------------


class TestUnsupported:
    """``unsupported`` works as a standalone error and as a partial gap."""

    def test_unsupported_standalone_error(self):
        result = unsupported(message="streaming not supported", locator="ws")
        assert isinstance(result, Error)
        assert result.kind is ErrorKind.UNSUPPORTED
        assert result.locator == "ws"

    def test_unsupported_gap_in_partial(self):
        g = unsupported_gap(locator="attachments", detail="binary blobs")
        result = partial(_tree(), [g])
        assert isinstance(result, Partial)
        assert result.tree.children  # partial still carries a real tree
        assert result.gaps[0].kind is ErrorKind.UNSUPPORTED
        assert result.gaps[0].locator == "attachments"

    def test_unsupported_distinct_from_not_found_in_results(self):
        unsup = unsupported()
        missing = error(ErrorKind.NOT_FOUND)
        assert unsup.kind is not missing.kind


# ---------------------------------------------------------------------------
# Partial carries tree + typed gaps
# ---------------------------------------------------------------------------


class TestPartial:
    """A partial result carries a real tree plus a typed gap list."""

    def test_partial_carries_tree_and_typed_gaps(self):
        gaps = [
            gap(ErrorKind.PERMISSION_DENIED, locator="child/a"),
            unsupported_gap(locator="child/b"),
        ]
        result = partial(_tree(), gaps)
        assert isinstance(result.tree, CompositionNode)
        assert all(isinstance(g, Gap) for g in result.gaps)
        assert {g.kind for g in result.gaps} == {
            ErrorKind.PERMISSION_DENIED,
            ErrorKind.UNSUPPORTED,
        }

    def test_partial_gaps_are_validated_as_typed(self):
        # A gap kind must be a real ErrorKind -- garbage is rejected at
        # construction (validation, not silent acceptance).
        with pytest.raises(Exception):
            Gap(kind="not-a-real-kind")


# ---------------------------------------------------------------------------
# match() and predicates dispatch correctly
# ---------------------------------------------------------------------------


class TestDispatch:
    """``match`` and the predicates branch on the right state."""

    def test_predicates_on_success(self):
        result = success(_tree())
        assert is_success(result)
        assert not is_partial(result)
        assert not is_error(result)

    def test_predicates_on_partial(self):
        result = partial(_tree(), [gap(ErrorKind.TRANSIENT)])
        assert is_partial(result)
        assert not is_success(result)
        assert not is_error(result)

    def test_predicates_on_error(self):
        result = error(ErrorKind.NOT_FOUND)
        assert is_error(result)
        assert not is_success(result)
        assert not is_partial(result)

    def test_match_dispatches_to_success_handler(self):
        out = match(
            success(_tree()),
            on_success=lambda s: ("s", len(s.tree.children)),
            on_partial=lambda p: ("p", len(p.gaps)),
            on_error=lambda e: ("e", e.kind),
        )
        assert out == ("s", 1)

    def test_match_dispatches_to_partial_handler(self):
        result = partial(_tree(), [gap(ErrorKind.TRANSIENT)])
        out = match(
            result,
            on_success=lambda s: "s",
            on_partial=lambda p: ("p", len(p.gaps)),
            on_error=lambda e: "e",
        )
        assert out == ("p", 1)

    def test_match_dispatches_to_error_handler(self):
        out = match(
            error(ErrorKind.AUTH_FAILED),
            on_success=lambda s: "s",
            on_partial=lambda p: "p",
            on_error=lambda e: e.kind,
        )
        assert out is ErrorKind.AUTH_FAILED

    def test_match_invokes_exactly_one_handler(self):
        calls: list[str] = []
        match(
            partial(_tree(), [gap(ErrorKind.RATE_LIMITED)]),
            on_success=lambda s: calls.append("s"),
            on_partial=lambda p: calls.append("p"),
            on_error=lambda e: calls.append("e"),
        )
        assert calls == ["p"]
