"""Single-fire deprecation warning for the legacy (pre-v1) layer."""

from __future__ import annotations

import warnings

_WARNED = False

_MESSAGE = (
    "The legacy omni_fetcher API (OmniFetcher, fetchers.*, and the "
    "pre-v1 schemas) is deprecated and will be removed in omni-fetcher "
    "2.0. Migrate to the canonical contract in omni_fetcher.v1 (see "
    "docs/migration-v1.md). Silence with: warnings.filterwarnings("
    "'ignore', category=DeprecationWarning, module='omni_fetcher')."
)


def warn_legacy_use() -> None:
    """Emit the legacy-layer ``DeprecationWarning`` once per process."""
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    warnings.warn(_MESSAGE, DeprecationWarning, stacklevel=3)
