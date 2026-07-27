"""External-behaviour tests for the v1 ``azure`` connector.

These tests exercise only the public contract surface -- ``stream()`` /
``fetch()`` returning a canonical ``Result`` -- with the azure-storage-blob
client fully stubbed via the ``_client`` seam. No real Azure call is ever made:
a fake client records the account name + key it was built with (so the per-call
``BasicAuth`` can be asserted) and replays a scripted ``download_blob`` or raises
a scripted azure error carrying an HTTP ``status_code``.

Covered:

- a text blob becomes a ``Success`` with a canonical ``kind`` ``"file"`` node
  whose content lives in a ``Text`` atom and whose descriptive fields live in
  ``source_extra["azure"]``;
- a CSV blob becomes a ``Table`` atom, an image an ``Image`` atom;
- HTTP 404/403/401/429 map onto NOT_FOUND / PERMISSION_DENIED / AUTH_FAILED /
  RATE_LIMITED;
- the per-call ``BasicAuth`` (account + key) is the credential used;
- a call with no/wrong auth is ``AUTH_FAILED`` without building a client;
- a missing extra is a typed ``UNSUPPORTED``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import BasicAuth, BearerAuth
from omni_fetcher.v1.connectors import azure_blob as azure_module
from omni_fetcher.v1.connectors.azure_blob import AzureBlobFetcher
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success


@pytest.fixture(autouse=True)
def _azure_available(monkeypatch):
    """Force the extra on so the seam-driven tests run without azure-storage-blob.

    These tests stub the client seam (``_client``), so no azure code is ever
    reached; only the availability gate would short-circuit them into an
    ``UNSUPPORTED`` before the logic under test runs. Tests that assert the
    *unavailable* path patch the flag back off.
    """
    monkeypatch.setattr(azure_module, "AZURE_AVAILABLE", True)


class _AzureError(Exception):
    """A minimal azure-core exception carrying an HTTP ``status_code``."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"azure error {status_code}")
        self.status_code = status_code


class _ContentSettings:
    def __init__(self, content_type: Optional[str]) -> None:
        self.content_type = content_type


class _BlobProperties:
    def __init__(
        self,
        *,
        content_type: Optional[str] = None,
        size: Optional[int] = None,
        etag: Optional[str] = None,
        last_modified: Optional[datetime] = None,
    ) -> None:
        self.content_settings = _ContentSettings(content_type)
        self.size = size
        self.etag = etag
        self.last_modified = last_modified


class _FakeDownloader:
    def __init__(self, data: bytes, properties: _BlobProperties) -> None:
        self._data = data
        self.properties = properties

    def readall(self) -> bytes:
        return self._data


class _FakeClient:
    def __init__(
        self,
        *,
        data: bytes = b"",
        properties: Optional[_BlobProperties] = None,
        raises: Optional[BaseException] = None,
    ) -> None:
        self._data = data
        self._properties = properties or _BlobProperties()
        self._raises = raises

    def download_blob(self) -> _FakeDownloader:
        if self._raises is not None:
            raise self._raises
        return _FakeDownloader(self._data, self._properties)


def _install(monkeypatch, client: _FakeClient, recorder: dict[str, Any]) -> None:
    """Patch the ``_client`` seam to return ``client``, recording the args."""

    def fake_client(
        container: str, blob: str, account: str, account_key: str, endpoint=None
    ) -> _FakeClient:
        recorder.update(
            container=container,
            blob=blob,
            account=account,
            account_key=account_key,
            endpoint=endpoint,
        )
        return client

    monkeypatch.setattr(AzureBlobFetcher, "_client", staticmethod(fake_client))


def _install_tripwire(monkeypatch) -> None:
    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("azure client must not be built")

    monkeypatch.setattr(AzureBlobFetcher, "_client", staticmethod(_explode))


_AUTH = BasicAuth(username="myaccount", password="key123")


async def test_text_blob_is_canonical_success(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        data=b"hello from azure",
        properties=_BlobProperties(
            content_type="text/plain",
            size=16,
            etag="0xETAG",
            last_modified=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
    )
    _install(monkeypatch, client, recorder)

    result = await AzureBlobFetcher().fetch("az://docs/notes.txt", auth=_AUTH)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "file"
    assert node.metadata.source_url == "az://docs/notes.txt"

    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "hello from azure"

    extra = node.metadata.source_extra["azure"]
    assert extra["account"] == "myaccount"
    assert extra["container"] == "docs"
    assert extra["blob"] == "notes.txt"
    assert extra["size"] == 16
    assert extra["etag"] == "0xETAG"
    assert extra["content_type"] == "text/plain"
    assert extra["last_modified"] == "2026-01-03T00:00:00+00:00"


async def test_csv_blob_becomes_table(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        data=b"a,b\n1,2\n3,4\n", properties=_BlobProperties(content_type="text/csv", size=12)
    )
    _install(monkeypatch, client, recorder)

    result = await AzureBlobFetcher().fetch("az://c/data.csv", auth=_AUTH)

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["a", "b"]
    assert atoms[0].rows == [["1", "2"], ["3", "4"]]


async def test_image_blob_becomes_image_atom(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        data=b"\x89PNG\r\n\x1a\n", properties=_BlobProperties(content_type="image/png", size=8)
    )
    _install(monkeypatch, client, recorder)

    result = await AzureBlobFetcher().fetch("az://c/pic.png", auth=_AUTH)

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert atoms[0].kind == AtomKind.IMAGE
    assert atoms[0].format == "png"


async def test_binary_unrepresented_blob_is_partial(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        data=b"\x00\x01\x02", properties=_BlobProperties(content_type="application/zip", size=3)
    )
    _install(monkeypatch, client, recorder)

    result = await AzureBlobFetcher().fetch("az://c/archive.zip", auth=_AUTH)

    assert isinstance(result, Partial)
    assert any(g.kind == ErrorKind.UNSUPPORTED for g in result.gaps)


async def test_per_call_basic_auth_is_used(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(data=b"x", properties=_BlobProperties(content_type="text/plain"))
    _install(monkeypatch, client, recorder)

    auth = BasicAuth(username="acct2", password="secretkey")
    result = await AzureBlobFetcher().fetch("azure://cont/path/obj.txt", auth=auth)

    assert isinstance(result, Success)
    assert recorder["account"] == "acct2"
    assert recorder["account_key"] == "secretkey"
    assert recorder["container"] == "cont"
    assert recorder["blob"] == "path/obj.txt"


async def test_missing_blob_is_not_found(monkeypatch):
    recorder: dict[str, Any] = {}
    _install(monkeypatch, _FakeClient(raises=_AzureError(404)), recorder)

    result = await AzureBlobFetcher().fetch("az://c/gone.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND
    assert result.locator == "az://c/gone.txt"


async def test_denied_is_permission_denied(monkeypatch):
    recorder: dict[str, Any] = {}
    _install(monkeypatch, _FakeClient(raises=_AzureError(403)), recorder)

    result = await AzureBlobFetcher().fetch("az://c/secret.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED


async def test_bad_key_is_auth_failed(monkeypatch):
    recorder: dict[str, Any] = {}
    _install(monkeypatch, _FakeClient(raises=_AzureError(401)), recorder)

    result = await AzureBlobFetcher().fetch("az://c/obj.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_throttled_is_rate_limited(monkeypatch):
    recorder: dict[str, Any] = {}
    _install(monkeypatch, _FakeClient(raises=_AzureError(429)), recorder)

    result = await AzureBlobFetcher().fetch("az://c/obj.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.RATE_LIMITED


async def test_missing_auth_is_auth_failed_without_client(monkeypatch):
    _install_tripwire(monkeypatch)

    result = await AzureBlobFetcher().fetch("az://c/obj.txt", auth=None)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_non_basic_auth_is_auth_failed(monkeypatch):
    _install_tripwire(monkeypatch)

    result = await AzureBlobFetcher().fetch("az://c/obj.txt", auth=BearerAuth(token="nope"))

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_invalid_uri_is_invalid_input(monkeypatch):
    recorder: dict[str, Any] = {}
    _install(monkeypatch, _FakeClient(), recorder)

    result = await AzureBlobFetcher().fetch("az://container-only", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_unsupported_when_extra_missing(monkeypatch):
    monkeypatch.setattr(azure_module, "AZURE_AVAILABLE", False)

    result = await AzureBlobFetcher().fetch("az://c/obj.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


def test_can_handle():
    assert AzureBlobFetcher.can_handle("az://container/key")
    assert AzureBlobFetcher.can_handle("azure://container/key")
    assert not AzureBlobFetcher.can_handle("s3://bucket/key")
    assert not AzureBlobFetcher.can_handle("gs://bucket/key")
