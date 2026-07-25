"""Real integration test for the v1 ``azure`` connector against Azurite.

Drives ``AzureBlobFetcher`` through its real ``azure-storage-blob`` path against
the official Azurite emulator, addressed via the connector's ``?endpoint=``
option (Azure's SDK has no endpoint env var). Uses Azurite's well-known account
and key. Skipped unless Azurite is reachable at ``$OMNI_TEST_AZURITE_ENDPOINT``
(default ``http://127.0.0.1:10000/devstoreaccount1``).

Spin one up with Docker:

    docker run -d --name omni-azurite -p 10000:10000 \
        mcr.microsoft.com/azure-storage/azurite azurite-blob --blobHost 0.0.0.0
"""

from __future__ import annotations

import os
from urllib.parse import quote

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import BasicAuth
from omni_fetcher.v1.connectors.azure_blob import AzureBlobFetcher
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

pytest.importorskip("azure.storage.blob", reason="the 'azure' extra is not installed")
from azure.core.credentials import AzureNamedKeyCredential  # noqa: E402
from azure.storage.blob import BlobServiceClient, ContentSettings  # noqa: E402

# Azurite's well-known development account and key.
_ACCOUNT = "devstoreaccount1"
_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
_ENDPOINT = os.environ.get("OMNI_TEST_AZURITE_ENDPOINT", f"http://127.0.0.1:10000/{_ACCOUNT}")
_CONTAINER = "omni-container"
_AUTH = BasicAuth(username=_ACCOUNT, password=_KEY)


@pytest.fixture
def blob():
    """Create a container + blob in Azurite; skip if unreachable."""
    try:
        service = BlobServiceClient(
            account_url=_ENDPOINT, credential=AzureNamedKeyCredential(_ACCOUNT, _KEY)
        )
        try:
            service.create_container(_CONTAINER)
        except Exception:
            pass
        client = service.get_blob_client(_CONTAINER, "hello.txt")
        client.upload_blob(
            b"hello from azure",
            overwrite=True,
            content_settings=ContentSettings(content_type="text/plain"),
        )
    except Exception as exc:  # noqa: BLE001 - any failure = emulator not usable
        pytest.skip(f"Azurite not usable at {_ENDPOINT}: {exc}")
    yield


def _uri(blob_name: str) -> str:
    return f"az://{_CONTAINER}/{blob_name}?endpoint={quote(_ENDPOINT, safe='')}"


async def test_read_text_blob(blob):
    result = await AzureBlobFetcher().fetch(_uri("hello.txt"), auth=_AUTH)

    assert isinstance(result, Success), result
    node = result.tree
    assert node.metadata.kind == "file"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "hello from azure"
    extra = node.metadata.source_extra["azure"]
    assert extra["account"] == _ACCOUNT
    assert extra["container"] == _CONTAINER
    assert extra["blob"] == "hello.txt"


async def test_missing_blob_is_not_found(blob):
    result = await AzureBlobFetcher().fetch(_uri("nope.txt"), auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND
