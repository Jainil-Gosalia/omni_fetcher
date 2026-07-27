"""Safe optional-dependency probing for connectors.

A connector module must import on a *base* install (without its optional extra),
deferring the real import to call time and exposing a ``<DEP>_AVAILABLE`` flag.
The obvious probe, ``importlib.util.find_spec(name) is not None``, is unsafe for a
**dotted** name: ``find_spec("azure.storage.blob")`` imports the parent packages
to locate the leaf, so it *raises* ``ModuleNotFoundError`` when ``azure`` is
absent rather than returning ``None`` -- which would break the module import.
:func:`module_available` wraps that and returns ``False`` instead.
"""

from __future__ import annotations

import importlib.util


def module_available(name: str) -> bool:
    """
    Report whether ``name`` is importable, without importing it

    Returns ``False`` (never raises) when the module -- or any parent package of
    a dotted name -- is absent, so a connector's module-level availability check
    is safe on a base install.

    Parameters
    ----------
        name:
            The (possibly dotted) module name to probe.

    Return
    ------
        available:
            ``True`` if a spec for ``name`` can be found, else ``False``.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
