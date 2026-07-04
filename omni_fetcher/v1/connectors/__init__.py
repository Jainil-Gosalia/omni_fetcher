"""OmniFetcher v1 connectors.

Each module here implements one source as a :class:`omni_fetcher.v1.BaseFetcher`
subclass: it overrides ``stream()`` to emit the canonical contract
(``CompositionNode`` trees built via ``omni_fetcher.v1.mapping``), wrapped in a
``Result``. Connectors are mutually independent and registered with an immutable
``Registry`` at integration time.
"""
