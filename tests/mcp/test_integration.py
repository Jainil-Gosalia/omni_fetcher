"""End-to-end MCP protocol tests for the OmniFetcher server.

Unlike ``test_server.py`` (which calls tool handlers in-process), these drive a
real ``ClientSession`` connected to the server through the MCP protocol via the
SDK's in-memory transport -- the same initialize / tools-list / tools-call
handshake a live client performs, without a subprocess. This proves the server
is wired correctly as an MCP server, not just that its tool functions work.

Extra-gating is verified in a subprocess so blocking the ``mcp`` import cannot
pollute this process's module state.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import AsyncIterator, Optional

import pytest

# Skip the whole module when the optional ``mcp`` extra is absent (D12).
pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from omni_fetcher.mcp.server import build_server  # noqa: E402
from omni_fetcher.v1 import BaseFetcher, RegistryBuilder, SourceDefinition  # noqa: E402
from omni_fetcher.v1.auth import AuthCredential  # noqa: E402
from omni_fetcher.v1.atoms import Text, TextFormat  # noqa: E402
from omni_fetcher.v1.mapping import build_node  # noqa: E402
from omni_fetcher.v1.result import Result, success  # noqa: E402

# No module-level asyncio mark: this file mixes async protocol tests with one
# sync subprocess test. The repo runs asyncio_mode="auto", so async tests are
# auto-marked and the sync test is left alone.


class _Prose(BaseFetcher):
    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: object = None
    ) -> AsyncIterator[Result]:
        yield success(
            build_node(kind="doc", atoms=[Text(content="Hello.", format=TextFormat.PLAIN)])
        )

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("prose://")


def _registry():
    return (
        RegistryBuilder()
        .add(SourceDefinition(name="prose", fetcher_class=_Prose, uri_patterns=("prose://*",)))
        .build()
    )


async def test_client_lists_the_tools_over_the_protocol() -> None:
    server = build_server(_registry())

    async with create_connected_server_and_client_session(server) as client:
        result = await client.list_tools()

    names = {t.name for t in result.tools}
    assert names == {"fetch", "sample", "list_sources"}


async def test_client_can_fetch_over_the_protocol() -> None:
    server = build_server(_registry())

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("fetch", {"uri": "prose://x"})

    payload = json.loads(result.content[0].text)
    assert payload["state"] == "success"


async def test_client_list_sources_over_the_protocol() -> None:
    server = build_server(_registry())

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("list_sources", {})

    payload = json.loads(result.content[0].text)
    assert payload["sources"][0]["name"] == "prose"


def test_import_without_the_extra_raises_naming_it() -> None:
    """With the ``mcp`` SDK unimportable, ``import omni_fetcher.mcp`` fails clearly.

    Run in a subprocess: a meta-path finder blocks the ``mcp`` package before
    the import, so this reproduces a core-only install without disturbing this
    test process (where ``mcp`` is installed).
    """
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'mcp' or name.startswith('mcp.'):\n"
        "            raise ImportError('blocked for test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "for m in list(sys.modules):\n"
        "    if m == 'mcp' or m.startswith('mcp.') or m == 'omni_fetcher.mcp':\n"
        "        del sys.modules[m]\n"
        "try:\n"
        "    import omni_fetcher.mcp\n"
        "    print('NO_ERROR')\n"
        "except ImportError as e:\n"
        "    print('IMPORTERROR:' + str(e))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
        timeout=60,
    )
    out = proc.stdout.strip()
    assert out.startswith("IMPORTERROR:"), f"unexpected: {out!r} / {proc.stderr[-400:]}"
    assert "mcp" in out and "extra" in out.lower()
