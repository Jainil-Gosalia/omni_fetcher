"""Behavior of the legacy-layer deprecation signal (v1.1 issue 002).

The legacy layer must warn exactly once per process on first use, while the
v1 package -- and a bare ``import omni_fetcher`` -- never trigger the
warning (the top-level package resolves legacy exports lazily). Process-level
semantics are asserted in fresh subprocesses.
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _run(code: str, *flags: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter rooted at the repo."""
    return subprocess.run(
        [sys.executable, *flags, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=_REPO,
    )


def test_v1_import_is_clean_under_error_filter() -> None:
    """`-W error::DeprecationWarning` does not break the v1 path."""
    proc = _run("import omni_fetcher.v1", "-W", "error::DeprecationWarning")
    assert proc.returncode == 0, proc.stderr


def test_bare_package_import_is_clean_under_error_filter() -> None:
    """Importing the package alone touches no legacy code and stays silent."""
    proc = _run("import omni_fetcher", "-W", "error::DeprecationWarning")
    assert proc.returncode == 0, proc.stderr


def test_legacy_export_access_warns() -> None:
    """Using a legacy export raises under the error filter, with guidance."""
    proc = _run(
        "from omni_fetcher import OmniFetcher",
        "-W",
        "error::DeprecationWarning",
    )
    assert proc.returncode != 0
    assert "deprecated" in proc.stderr
    assert "omni_fetcher.v1" in proc.stderr
    assert "2.0" in proc.stderr


def test_direct_legacy_module_import_warns_exactly_once() -> None:
    """Direct legacy imports + lazy exports fire one warning total."""
    code = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import omni_fetcher.fetchers\n"
        "    import omni_fetcher.fetcher\n"
        "    from omni_fetcher import OmniFetcher\n"
        "hits = [w for w in caught\n"
        "        if issubclass(w.category, DeprecationWarning)\n"
        "        and 'omni_fetcher.v1' in str(w.message)]\n"
        "print(len(hits))\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1"


def test_star_import_still_provides_legacy_names() -> None:
    """``from omni_fetcher import *`` keeps working through the lazy layer."""
    code = (
        "import warnings\n"
        "warnings.simplefilter('ignore', DeprecationWarning)\n"
        "from omni_fetcher import *\n"
        "print(OmniFetcher.__name__, TextDocument.__name__)\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["OmniFetcher", "TextDocument"]


def test_lazy_exports_resolve_to_the_real_objects() -> None:
    """Attribute access returns the genuine legacy classes."""
    import omni_fetcher

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        resolved = omni_fetcher.TextDocument

    from omni_fetcher.schemas.atomics import TextDocument

    assert resolved is TextDocument
    assert "OmniFetcher" in dir(omni_fetcher)


def test_unknown_attribute_raises_attribute_error() -> None:
    """Names outside the legacy export map fail loudly."""
    import omni_fetcher

    with pytest.raises(AttributeError):
        omni_fetcher.NoSuchThing  # noqa: B018
