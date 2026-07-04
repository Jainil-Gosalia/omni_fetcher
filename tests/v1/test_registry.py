"""External-behaviour tests for the immutable v1 registry.

These tests exercise only public behaviour: routing correctness (pattern
match plus priority tie-break), immutability after build, the absence of any
shared global mutable state, and the unknown-URI case.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

import pytest

from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.registry import (
    FrozenRegistry,
    Registry,
    RegistryBuilder,
    SourceDefinition,
)
from omni_fetcher.v1.result import Result
from omni_fetcher.v1.zoom import ZoomSpec


class _DummyFetcher(BaseFetcher):
    """A minimal concrete fetcher used only for routing assertions."""

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        if False:  # pragma: no cover - never iterated in these tests
            yield  # type: ignore[unreachable]


class AlphaFetcher(_DummyFetcher):
    """First distinct dummy fetcher."""


class BetaFetcher(_DummyFetcher):
    """Second distinct dummy fetcher."""


class GammaFetcher(_DummyFetcher):
    """Third distinct dummy fetcher."""


def _definition(
    name: str,
    fetcher_class: type[BaseFetcher],
    patterns: tuple[str, ...],
    priority: int = 100,
) -> SourceDefinition:
    return SourceDefinition(
        name=name,
        fetcher_class=fetcher_class,
        uri_patterns=patterns,
        priority=priority,
    )


# ----------------------------------------------------------------------
# Routing correctness
# ----------------------------------------------------------------------


def test_resolve_matches_glob_pattern():
    # fnmatch globs match the whole URI, so an interior host match needs
    # leading/trailing wildcards.
    registry = FrozenRegistry((_definition("gh", AlphaFetcher, ("*github.com/*",)),))

    assert registry.resolve("https://github.com/foo/bar") is AlphaFetcher


def test_resolve_matches_substring_pattern():
    registry = FrozenRegistry((_definition("notion", AlphaFetcher, ("notion.so",)),))

    assert registry.resolve("https://www.notion.so/page") is AlphaFetcher


def test_resolve_matches_regex_pattern():
    registry = FrozenRegistry((_definition("re", AlphaFetcher, (r"\.pdf$",)),))

    assert registry.resolve("file:///docs/report.pdf") is AlphaFetcher
    assert registry.resolve("file:///docs/report.txt") is None


def test_unknown_uri_returns_none():
    registry = FrozenRegistry((_definition("gh", AlphaFetcher, ("github.com/*",)),))

    assert registry.resolve("https://example.com/nope") is None
    assert registry.definition_for("https://example.com/nope") is None


def test_empty_registry_resolves_to_none():
    registry = FrozenRegistry()

    assert registry.resolve("anything") is None
    assert registry.definitions() == ()


def test_priority_breaks_ties_lower_wins():
    # Both definitions match the same URI; the lower priority must win,
    # regardless of insertion order.
    registry = FrozenRegistry(
        (
            _definition("low_prio", BetaFetcher, ("*",), priority=200),
            _definition("high_prio", AlphaFetcher, ("*",), priority=10),
        )
    )

    assert registry.resolve("anything") is AlphaFetcher
    definition = registry.definition_for("anything")
    assert definition is not None
    assert definition.name == "high_prio"


def test_definition_for_returns_matching_definition():
    target = _definition("gh", AlphaFetcher, ("*github.com/*",))
    registry = FrozenRegistry((target,))

    assert registry.definition_for("https://github.com/x") == target


def test_definitions_ordered_by_priority():
    registry = FrozenRegistry(
        (
            _definition("c", GammaFetcher, ("c",), priority=300),
            _definition("a", AlphaFetcher, ("a",), priority=100),
            _definition("b", BetaFetcher, ("b",), priority=200),
        )
    )

    names = [d.name for d in registry.definitions()]
    assert names == ["a", "b", "c"]


def test_duplicate_names_rejected():
    with pytest.raises(ValueError):
        FrozenRegistry(
            (
                _definition("dup", AlphaFetcher, ("a",)),
                _definition("dup", BetaFetcher, ("b",)),
            )
        )


# ----------------------------------------------------------------------
# Immutability / read-only after build
# ----------------------------------------------------------------------


def test_registry_satisfies_registry_protocol():
    registry = FrozenRegistry()

    assert isinstance(registry, Registry)


def test_cannot_set_attribute_after_build():
    registry = FrozenRegistry((_definition("gh", AlphaFetcher, ("github.com/*",)),))

    with pytest.raises(AttributeError):
        registry._definitions = ()  # type: ignore[misc]

    with pytest.raises(AttributeError):
        registry.new_field = 1  # type: ignore[attr-defined]


def test_cannot_delete_attribute_after_build():
    registry = FrozenRegistry((_definition("gh", AlphaFetcher, ("github.com/*",)),))

    with pytest.raises(AttributeError):
        del registry._definitions


def test_definitions_view_is_immutable_tuple():
    registry = FrozenRegistry((_definition("gh", AlphaFetcher, ("github.com/*",)),))

    definitions = registry.definitions()
    assert isinstance(definitions, tuple)
    with pytest.raises(TypeError):
        definitions[0] = None  # type: ignore[index]


def test_source_definition_is_frozen():
    definition = _definition("gh", AlphaFetcher, ("github.com/*",))

    with pytest.raises(Exception):
        definition.priority = 1  # type: ignore[misc]


def test_mutating_source_collection_does_not_affect_registry():
    defs = [_definition("gh", AlphaFetcher, ("github.com/*",))]
    registry = FrozenRegistry(tuple(defs))

    defs.append(_definition("other", BetaFetcher, ("other",)))

    assert len(registry.definitions()) == 1


# ----------------------------------------------------------------------
# Builder: declaration without a global mutable singleton
# ----------------------------------------------------------------------


def test_builder_decorator_registers_source():
    builder = RegistryBuilder()

    @builder.source(name="alpha", uri_patterns=("*alpha.example/*",))
    class _Decorated(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]

    registry = builder.build()

    # Decorator returns the class unchanged.
    assert registry.resolve("https://alpha.example/x") is _Decorated


def test_builder_add_is_chainable():
    builder = RegistryBuilder()
    result = builder.add(_definition("a", AlphaFetcher, ("a",))).add(
        _definition("b", BetaFetcher, ("b",))
    )

    assert result is builder
    registry = builder.build()
    assert {d.name for d in registry.definitions()} == {"a", "b"}


def test_builders_do_not_share_state():
    # Two independent builders prove there is no shared global registry.
    builder_one = RegistryBuilder()
    builder_one.add(_definition("only_one", AlphaFetcher, ("one",)))

    builder_two = RegistryBuilder()
    builder_two.add(_definition("only_two", BetaFetcher, ("two",)))

    registry_one = builder_one.build()
    registry_two = builder_two.build()

    assert [d.name for d in registry_one.definitions()] == ["only_one"]
    assert [d.name for d in registry_two.definitions()] == ["only_two"]
    assert registry_one.resolve("two") is None
    assert registry_two.resolve("one") is None


def test_build_snapshots_so_later_mutation_is_isolated():
    builder = RegistryBuilder()
    builder.add(_definition("a", AlphaFetcher, ("a",)))
    registry = builder.build()

    # Mutating the builder after build must not change the built registry.
    builder.add(_definition("b", BetaFetcher, ("b",)))

    assert [d.name for d in registry.definitions()] == ["a"]


def test_no_module_level_mutable_registry_singleton():
    # Importing the module twice must not accumulate any shared definitions.
    import omni_fetcher.v1.registry as registry_module

    public = set(dir(registry_module))
    forbidden = {"_REGISTRY", "REGISTRY", "_registry", "_GLOBAL_REGISTRY"}
    assert public.isdisjoint(forbidden)
