"""The immutable source-definition registry interface for v1.

The registry holds stateless source *definitions* only -- URI patterns and
the fetcher class that handles them -- and is read-only after registration.
There is no process-global mutable singleton and nothing credential-bearing
or fetched is held here (see ``PHILOSOPHY.md`` sections 6 and 7, and the v1
plan's registry decision).

This file fixes the definition value type and the ``Registry`` interface,
and provides the concrete immutable implementation
(``FrozenRegistry``) plus a build-then-freeze helper
(``RegistryBuilder``) for declaring source definitions without any
process-global mutable singleton.

Intended usage
--------------
Build a registry once at wiring time and treat it as read-only thereafter::

    from omni_fetcher.v1.registry import RegistryBuilder

    builder = RegistryBuilder()

    @builder.source(name="github", uri_patterns=("github.com/*",))
    class GitHubFetcher(BaseFetcher):
        ...

    registry = builder.build()  # immutable FrozenRegistry
    fetcher_class = registry.resolve("https://github.com/foo/bar")

The builder is a transient, local object owned by the caller; the
``FrozenRegistry`` it produces is immutable. Nothing here is a module-level
mutable singleton, so two builders never share state and tests need no
global reset.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Callable, Optional, Protocol, Type, runtime_checkable

from pydantic import BaseModel, ConfigDict

from omni_fetcher.v1.fetcher import BaseFetcher


class SourceDefinition(BaseModel):
    """
    An immutable source definition
    ===============================================
    A stateless, read-only record describing how to route a URI to a
    fetcher. Holds no credentials and no fetched data.
    ===============================================

    Attributes
    ----------
        name:
            Unique source name.
        fetcher_class:
            The ``BaseFetcher`` subclass that handles matching URIs.
        uri_patterns:
            URI patterns this source claims.
        priority:
            Lower is higher priority when multiple definitions match.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    fetcher_class: Type[BaseFetcher]
    uri_patterns: tuple[str, ...] = ()
    priority: int = 100


@runtime_checkable
class Registry(Protocol):
    """
    Interface for immutable source-definition lookup
    ===============================================
    Resolves a URI to the fetcher that handles it, over an immutable set of
    source definitions fixed at registration time. Read-only: no mutation
    methods, no shared mutable state.
    ===============================================
    NOTE:
        1. ``FrozenRegistry`` is the concrete implementation in this module.

    Methods
    -------
        resolve:
        definition_for:
        definitions:
    """

    def resolve(self, uri: str) -> Optional[Type[BaseFetcher]]:
        """
        Resolve a URI to its handling fetcher class

        Parameters
        ----------
            uri:
                The source URI to route.

        Return
        ------
            fetcher_class:
                The highest-priority matching fetcher class, or ``None`` if
                none matches.
        """
        ...

    def definition_for(self, uri: str) -> Optional[SourceDefinition]:
        """
        Find the source definition that handles a URI

        Parameters
        ----------
            uri:
                The source URI to route.

        Return
        ------
            definition:
                The highest-priority matching definition, or ``None``.
        """
        ...

    def definitions(self) -> tuple[SourceDefinition, ...]:
        """
        List all registered source definitions

        Return
        ------
            definitions:
                An immutable tuple of all registered definitions.
        """
        ...


def _matches_uri(pattern: str, uri: str) -> bool:
    """Report whether a single pattern claims a URI.

    An explicit ``re:`` prefix forces regex matching -- required for
    regexes containing fnmatch trigger characters (``https?``, lookaheads).
    Otherwise: fnmatch glob when the pattern contains ``*``/``?``/``[``,
    else regex search, else plain substring containment.
    """
    if pattern.startswith("re:"):
        return re.search(pattern[3:], uri) is not None
    if "*" in pattern or "?" in pattern or "[" in pattern:
        return fnmatch.fnmatch(uri, pattern)
    if re.search(pattern, uri):
        return True
    return pattern in uri


class FrozenRegistry:
    """
    An immutable, read-only source-definition registry
    ===============================================
    Holds a fixed set of stateless ``SourceDefinition`` records and routes a
    URI to the fetcher that handles it. Construction takes a snapshot of the
    definitions and the registry exposes no mutation methods, so it is
    read-only after construction: no global mutable singleton, no
    credentials, and no fetched data are ever stored (see ``PHILOSOPHY.md``
    sections 6, 7 and 18, decisions #11 and #18).
    ===============================================
    NOTE:
        1. Routing matches a URI against each definition's ``uri_patterns``
           and breaks ties by ``priority`` (lower wins), then by registration
           order, giving deterministic resolution.
        2. Build one with ``RegistryBuilder`` (decorator/registration style)
           or directly from a collection of definitions.

    Attributes
    ----------
        _definitions:
            The frozen tuple of source definitions, sorted by routing
            precedence.

    Methods
    -------
        resolve:
        definition_for:
        definitions:
    """

    __slots__ = ("_definitions",)

    # Declared for type checkers: the value is installed in __init__ via
    # object.__setattr__ to bypass this class's immutability guard.
    _definitions: tuple[SourceDefinition, ...]

    def __init__(
        self,
        definitions: tuple[SourceDefinition, ...] = (),
    ) -> None:
        """
        Build an immutable registry from a collection of definitions

        Parameters
        ----------
            definitions:
                The source definitions to hold. They are copied into an
                internal tuple sorted by routing precedence, so the caller's
                collection is never retained or mutated.
        """
        names: set[str] = set()
        for definition in definitions:
            if definition.name in names:
                raise ValueError(f"duplicate source definition name: {definition.name!r}")
            names.add(definition.name)

        # Stable sort by priority (lower = higher priority); ties keep the
        # caller's registration order, giving deterministic resolution.
        ordered = sorted(definitions, key=lambda d: d.priority)
        object.__setattr__(self, "_definitions", tuple(ordered))

    def __setattr__(self, name: str, value: object) -> None:
        """Reject all attribute mutation -- the registry is read-only."""
        raise AttributeError("FrozenRegistry is immutable; build a new one instead")

    def __delattr__(self, name: str) -> None:
        """Reject all attribute deletion -- the registry is read-only."""
        raise AttributeError("FrozenRegistry is immutable; build a new one instead")

    def definition_for(self, uri: str) -> Optional[SourceDefinition]:
        """
        Find the source definition that handles a URI

        Parameters
        ----------
            uri:
                The source URI to route.

        Return
        ------
            definition:
                The highest-priority matching definition, or ``None`` if no
                definition claims the URI.
        """
        for definition in self._definitions:
            for pattern in definition.uri_patterns:
                if _matches_uri(pattern, uri):
                    return definition
        return None

    def resolve(self, uri: str) -> Optional[Type[BaseFetcher]]:
        """
        Resolve a URI to its handling fetcher class

        Parameters
        ----------
            uri:
                The source URI to route.

        Return
        ------
            fetcher_class:
                The highest-priority matching fetcher class, or ``None`` if
                none matches.
        """
        definition = self.definition_for(uri)
        if definition is None:
            return None
        return definition.fetcher_class

    def definitions(self) -> tuple[SourceDefinition, ...]:
        """
        List all registered source definitions

        Return
        ------
            definitions:
                An immutable tuple of all registered definitions, ordered by
                routing precedence (priority ascending, then registration
                order).
        """
        return self._definitions


class RegistryBuilder:
    """
    A transient builder that produces an immutable registry
    ===============================================
    Collects source definitions -- via the ``source`` decorator or the
    ``add`` method -- and produces a read-only ``FrozenRegistry`` from them.
    The builder is the only mutable object, it is local to the caller, and it
    is discarded once ``build`` is called. There is no module-level mutable
    singleton, so distinct builders never share state.
    ===============================================
    NOTE:
        1. Each ``RegistryBuilder`` instance is independent; nothing is
           registered into a process-global collection.
        2. ``build`` snapshots the accumulated definitions, so mutating the
           builder afterwards never affects an already-built registry.

    Attributes
    ----------
        _definitions:
            The list of definitions accumulated so far.

    Methods
    -------
        add:
        source:
        build:
    """

    def __init__(self) -> None:
        """Create an empty builder owning no shared state."""
        self._definitions: list[SourceDefinition] = []

    def add(self, definition: SourceDefinition) -> RegistryBuilder:
        """
        Add a pre-built source definition

        Parameters
        ----------
            definition:
                The stateless ``SourceDefinition`` to register.

        Return
        ------
            builder:
                This builder, to allow chaining.
        """
        self._definitions.append(definition)
        return self

    def source(
        self,
        name: str,
        uri_patterns: tuple[str, ...] = (),
        priority: int = 100,
    ) -> Callable[[Type[BaseFetcher]], Type[BaseFetcher]]:
        """
        Decorate a fetcher class to declare it as a source

        Parameters
        ----------
            name:
                Unique source name.
            uri_patterns:
                URI patterns this source claims.
            priority:
                Lower is higher priority when multiple definitions match.

        Return
        ------
            decorator:
                A decorator that records the definition and returns the
                fetcher class unchanged (no instance mutation).
        """

        def decorator(
            fetcher_class: Type[BaseFetcher],
        ) -> Type[BaseFetcher]:
            self.add(
                SourceDefinition(
                    name=name,
                    fetcher_class=fetcher_class,
                    uri_patterns=uri_patterns,
                    priority=priority,
                )
            )
            return fetcher_class

        return decorator

    def build(self) -> FrozenRegistry:
        """
        Produce an immutable registry from the accumulated definitions

        Return
        ------
            registry:
                A read-only ``FrozenRegistry`` snapshot. Subsequent builder
                mutation does not affect it.
        """
        return FrozenRegistry(tuple(self._definitions))
