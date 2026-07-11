"""v1 CLI behavior tests (issue 012).

Drives the ``omni-fetcher v1 fetch`` command through typer's CliRunner --
local files only, no network:

- a local file renders as a tree and exits 0;
- ``--json`` emits contract JSON that round-trips through the Result model;
- an unrouted URI exits 1 with the typed error rendered;
- a missing credential environment variable is a usage error (exit 2), and
  the secret value itself never appears in output;
- ``--zoom text=paragraph`` decomposes through the CLI path;
- the command is mounted on the legacy app under the ``v1`` namespace.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from omni_fetcher.v1.cli import v1_app

runner = CliRunner()


@pytest.fixture()
def sample_md(tmp_path: Path) -> str:
    doc = tmp_path / "notes.md"
    doc.write_text(
        "# Title\n\nFirst paragraph from the cli.\n\nSecond paragraph.\n",
        encoding="utf-8",
    )
    return str(doc)


def test_fetch_local_file_renders_tree_and_exits_zero(sample_md: str) -> None:
    """The happy path prints a tree for a local file."""
    result = runner.invoke(v1_app, ["fetch", sample_md])

    assert result.exit_code == 0, result.output
    assert "file" in result.output
    assert "First paragraph from the cli." in result.output


def test_json_output_round_trips_through_the_contract(sample_md: str) -> None:
    """--json emits Result JSON with the success state and the tree."""
    result = runner.invoke(v1_app, ["fetch", sample_md, "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["state"] == "success"
    assert payload["tree"]["metadata"]["kind"] == "file"


def test_unrouted_uri_exits_one_with_typed_error() -> None:
    """An unknown scheme renders NOT_FOUND and exits 1."""
    result = runner.invoke(v1_app, ["fetch", "nope://x"])

    assert result.exit_code == 1
    assert "not_found" in result.output


def test_missing_credential_env_is_a_usage_error(sample_md: str) -> None:
    """--auth-type bearer with an unset env var exits 2, naming the var."""
    result = runner.invoke(
        v1_app,
        ["fetch", sample_md, "--auth-type", "bearer", "--token-env", "OMNI_NO_SUCH"],
    )

    assert result.exit_code == 2
    assert "OMNI_NO_SUCH" in result.output


def test_secret_values_never_appear_in_output(
    sample_md: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env-var *value* stays out of stdout/stderr entirely."""
    monkeypatch.setenv("OMNI_CLI_TOKEN", "sup3r-secret-value")

    result = runner.invoke(
        v1_app,
        ["fetch", sample_md, "--auth-type", "bearer", "--token-env", "OMNI_CLI_TOKEN"],
    )

    assert result.exit_code == 0, result.output
    assert "sup3r-secret-value" not in result.output


def test_zoom_flag_decomposes_through_the_cli(sample_md: str) -> None:
    """--zoom text=paragraph produces paragraph nodes in the JSON output."""
    result = runner.invoke(v1_app, ["fetch", sample_md, "--zoom", "text=paragraph", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    kinds = [child["metadata"]["kind"] for child in payload["tree"]["children"]]
    assert kinds.count("paragraph") >= 2


def test_bad_zoom_entry_is_a_usage_error(sample_md: str) -> None:
    """A malformed --zoom value exits 2 with guidance."""
    result = runner.invoke(v1_app, ["fetch", sample_md, "--zoom", "text=bogus"])

    assert result.exit_code == 2
    assert "zoom" in result.output.lower()


def test_v1_namespace_is_mounted_on_the_legacy_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`omni-fetcher v1 fetch --help` works through the legacy entry point."""
    import importlib
    import sys
    import warnings

    # The v1 conftest installs a bare parent package for isolation; the
    # legacy CLI needs the real one (whose exports are lazy and light).
    bare = sys.modules.get("omni_fetcher")
    if bare is not None and not getattr(bare, "__file__", None):
        monkeypatch.delitem(sys.modules, "omni_fetcher")
        importlib.import_module("omni_fetcher")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from omni_fetcher.cli import app as legacy_app

    result = runner.invoke(legacy_app, ["v1", "fetch", "--help"])

    assert result.exit_code == 0
    assert "URI" in result.output
