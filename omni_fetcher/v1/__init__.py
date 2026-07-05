"""OmniFetcher v1 canonical contract.

The single, stable, typed output contract every source maps onto:

- Canonical content atoms (``Text``, ``Image``, ``Audio``, ``Video``,
  ``Table``) -- content only.
- ``CompositionNode`` -- the recursive composition tree.
- ``Metadata`` -- the typed descriptive channel (core + ``source_extra``).
- ``Result`` -- the ``success`` / ``partial`` / ``error`` envelope, with the
  ``ErrorKind`` taxonomy and typed ``Gap`` list.
- ``ZoomSpec`` -- per-atom-type semantic decomposition depth.
- ``BaseFetcher`` -- the ``stream()`` primitive + base-provided ``fetch()``.
- ``AuthResolver`` / ``Registry`` -- per-call auth and immutable routing
  interfaces.

This package is ADDITIVE: it coexists with the v0.11 contract until a later
issue removes the old one.
"""

from __future__ import annotations

from omni_fetcher.v1.atoms import (
    AnyAtom,
    Atom,
    AtomKind,
    Audio,
    Image,
    Table,
    Text,
    TextFormat,
    Video,
)
from omni_fetcher.v1.auth import (
    ApiKeyAuth,
    AuthCredential,
    AuthResolver,
    AuthType,
    AwsAuth,
    BasicAuth,
    BearerAuth,
    OAuth2Auth,
)
from omni_fetcher.v1.builtin import builtin_registry
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.metadata import (
    Metadata,
    Permissions,
    TemporalPosition,
)
from omni_fetcher.v1.node import CompositionNode, NodeChild
from omni_fetcher.v1.orchestrator import OmniFetcher
from omni_fetcher.v1.registry import (
    FrozenRegistry,
    Registry,
    RegistryBuilder,
    SourceDefinition,
)
from omni_fetcher.v1.result import (
    Error,
    Gap,
    Partial,
    Result,
    ResultState,
    Success,
    error,
    is_error,
    is_partial,
    is_success,
    match,
    partial,
    success,
)
from omni_fetcher.v1.retry import RetryPolicy, fetch_with_retry
from omni_fetcher.v1.zoom import DepthLevel, ZoomSpec, prune_result, prune_to_zoom

__all__ = [
    # Atoms
    "Atom",
    "AtomKind",
    "AnyAtom",
    "Text",
    "TextFormat",
    "Image",
    "Audio",
    "Video",
    "Table",
    # Composition tree
    "CompositionNode",
    "NodeChild",
    # Metadata channel
    "Metadata",
    "Permissions",
    "TemporalPosition",
    # Result envelope
    "Result",
    "ResultState",
    "Success",
    "Partial",
    "Error",
    "Gap",
    "success",
    "partial",
    "error",
    "is_success",
    "is_partial",
    "is_error",
    "match",
    # Error taxonomy
    "ErrorKind",
    # Zoom
    "ZoomSpec",
    "DepthLevel",
    "prune_to_zoom",
    "prune_result",
    # Retry
    "RetryPolicy",
    "fetch_with_retry",
    # Fetcher
    "BaseFetcher",
    # Auth
    "AuthType",
    "AuthCredential",
    "AuthResolver",
    "BearerAuth",
    "ApiKeyAuth",
    "BasicAuth",
    "OAuth2Auth",
    "AwsAuth",
    # Registry + wiring
    "Registry",
    "SourceDefinition",
    "FrozenRegistry",
    "RegistryBuilder",
    "builtin_registry",
    # Orchestrator
    "OmniFetcher",
]
