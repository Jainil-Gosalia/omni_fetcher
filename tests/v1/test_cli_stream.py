"""v1 CLI ``stream`` command tests (issue 05).

Drives ``omni-fetcher v1 stream`` through typer's CliRunner against local
tail fixtures -- no network, no hang (``--max-items`` bounds every run):

- NDJSON output: one Result JSON per line, parseable back through the model;
- ``--max-items`` stops cleanly at exit 0;
- an unrouted URI ends with a typed error and exit 1;
- missing credential env vars are usage errors (exit 2) and secret values
  never appear in output;
- Ctrl-C exits 130 after cleanup;
- legacy commands and ``v1 fetch`` are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from omni_fetcher.v1 import cli as cli_module
from omni_fetcher.v1.cli import v1_app

runner = CliRunner()


@pytest.fixture()
def sample_log(tmp_path: Path) -> str:
    log = tmp_path / "app.log"
    log.write_text("line one\nline two\nline three\n", encoding="utf-8", newline="")
    return "tail://" + str(log).replace("\\", "/") + "?from=start&poll=0.01"


def test_stream_emits_ndjson_and_exits_zero(sample_log: str) -> None:
    """--max-items N prints exactly N parseable NDJSON lines, exit 0."""
    result = runner.invoke(v1_app, ["stream", sample_log, "--max-items", "3"])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("{")]
    assert len(lines) == 3
    payloads = [json.loads(line) for line in lines]
    assert all(p["state"] == "success" for p in payloads)
    assert [p["tree"]["metadata"]["kind"] for p in payloads] == ["log_line"] * 3
    assert payloads[0]["tree"]["children"][0]["content"] == "line one"


def test_max_items_bounds_a_following_stream(sample_log: str) -> None:
    """--max-items stops a would-be-unbounded tail without hanging."""
    result = runner.invoke(v1_app, ["stream", sample_log, "--max-items", "2"])

    assert result.exit_code == 0, result.output
    assert len([line for line in result.output.splitlines() if line.startswith("{")]) == 2


def test_unrouted_uri_ends_with_error_exit_one() -> None:
    """An unknown scheme streams one typed error and exits 1."""
    result = runner.invoke(v1_app, ["stream", "nope://x", "--max-items", "5"])

    assert result.exit_code == 1
    assert "not_found" in result.output


def test_missing_credential_env_is_usage_error(sample_log: str) -> None:
    """--auth-type bearer with an unset env var exits 2, naming the var."""
    result = runner.invoke(
        v1_app,
        ["stream", sample_log, "--auth-type", "bearer", "--token-env", "OMNI_ABSENT"],
    )

    assert result.exit_code == 2
    assert "OMNI_ABSENT" in result.output


def test_secret_values_never_appear_in_output(
    sample_log: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env-var value stays out of stdout/stderr entirely."""
    monkeypatch.setenv("OMNI_STREAM_TOKEN", "sup3r-secret-stream")

    result = runner.invoke(
        v1_app,
        [
            "stream",
            sample_log,
            "--auth-type",
            "bearer",
            "--token-env",
            "OMNI_STREAM_TOKEN",
            "--max-items",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "sup3r-secret-stream" not in result.output


def test_keyboard_interrupt_exits_130(sample_log: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C during streaming exits 130 after the cleanup path runs."""

    def _raise(coro: Any) -> int:
        coro.close()  # close the unawaited coroutine to silence the warning
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.asyncio, "run", _raise)

    result = runner.invoke(v1_app, ["stream", sample_log, "--max-items", "1"])

    assert result.exit_code == 130


def test_stream_is_mounted_beside_fetch(sample_log: str) -> None:
    """Both v1 subcommands coexist; --help lists stream."""
    result = runner.invoke(v1_app, ["--help"])

    assert result.exit_code == 0
    assert "stream" in result.output and "fetch" in result.output
