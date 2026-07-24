"""External-behaviour tests for the v1 ``gcs`` connector.

These tests exercise only the public contract surface -- ``stream()`` /
``fetch()`` returning a canonical ``Result`` -- with the google-cloud-storage
client fully stubbed via the ``_client`` seam. No real GCS call is ever made: a
fake client records the access token it was built with (so the per-call
``OAuth2Auth`` can be asserted) and replays a scripted ``download_as_bytes`` or
raises a scripted google error carrying an HTTP ``code``.

Covered:

- a text object becomes a ``Success`` with a canonical ``kind`` ``"file"`` node
  whose content lives in a ``Text`` atom and whose descriptive fields live in
  ``source_extra["gcs"]``;
- a CSV object becomes a ``Table`` atom, an image an ``Image`` atom;
- HTTP 404/403/401/429 map onto NOT_FOUND / PERMISSION_DENIED / AUTH_FAILED /
  RATE_LIMITED;
- the per-call ``OAuth2Auth`` token is the credential used (not ambient);
- a call with no/wrong auth is ``AUTH_FAILED`` without building a client;
- a missing extra is a typed ``UNSUPPORTED``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import BearerAuth, OAuth2Auth
from omni_fetcher.v1.connectors import gcs as gcs_module
from omni_fetcher.v1.connectors.gcs import GCSFetcher
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success


class _GoogleError(Exception):
    """A minimal google-cloud exception carrying an HTTP status ``code``."""

    def __init__(self, code: int) -> None:
        super().__init__(f"google error {code}")
        self.code = code


class _FakeBlob:
    """A google-cloud ``Blob`` stand-in populated as a download would populate it."""

    def __init__(
        self,
        *,
        data: bytes = b"",
        content_type: Optional[str] = None,
        size: Optional[int] = None,
        etag: Optional[str] = None,
        updated: Optional[datetime] = None,
        raises: Optional[BaseException] = None,
    ) -> None:
        self._data = data
        self._raises = raises
        self.content_type = content_type
        self.size = size
        self.etag = etag
        self.updated = updated

    def download_as_bytes(self) -> bytes:
        if self._raises is not None:
            raise self._raises
        return self._data


class _FakeBucket:
    def __init__(self, blob: _FakeBlob, recorder: dict[str, Any]) -> None:
        self._blob = blob
        self._recorder = recorder

    def blob(self, key: str) -> _FakeBlob:
        self._recorder["key"] = key
        return self._blob


class _FakeClient:
    def __init__(self, blob: _FakeBlob, recorder: dict[str, Any]) -> None:
        self._blob = blob
        self._recorder = recorder

    def bucket(self, name: str) -> _FakeBucket:
        self._recorder["bucket"] = name
        return _FakeBucket(self._blob, self._recorder)


def _install(monkeypatch, blob: _FakeBlob, recorder: dict[str, Any]) -> None:
    """Patch the ``_client`` seam to return a fake client, recording the token."""

    def fake_client(access_token: str) -> _FakeClient:
        recorder["access_token"] = access_token
        return _FakeClient(blob, recorder)

    monkeypatch.setattr(GCSFetcher, "_client", staticmethod(fake_client))


def _install_tripwire(monkeypatch) -> None:
    """Patch ``_client`` with a tripwire that must never be reached."""

    def _explode(access_token: str) -> Any:
        raise AssertionError("gcs client must not be built")

    monkeypatch.setattr(GCSFetcher, "_client", staticmethod(_explode))


_AUTH = OAuth2Auth(access_token="ya29.test-token")


async def test_text_object_is_canonical_success(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(
        data=b"hello from gcs",
        content_type="text/plain",
        size=14,
        etag="abc123",
        updated=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://my-bucket/notes.txt", auth=_AUTH)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "file"
    assert node.metadata.source_url == "gs://my-bucket/notes.txt"

    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "hello from gcs"

    extra = node.metadata.source_extra["gcs"]
    assert extra["bucket"] == "my-bucket"
    assert extra["object"] == "notes.txt"
    assert extra["size"] == 14
    assert extra["etag"] == "abc123"
    assert extra["content_type"] == "text/plain"
    assert extra["updated"] == "2026-01-02T00:00:00+00:00"


async def test_csv_object_becomes_table(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(data=b"a,b\n1,2\n3,4\n", content_type="text/csv", size=12)
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://bkt/data.csv", auth=_AUTH)

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["a", "b"]
    assert atoms[0].rows == [["1", "2"], ["3", "4"]]


async def test_image_object_becomes_image_atom(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(data=b"\x89PNG\r\n\x1a\n", content_type="image/png", size=8)
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://bkt/pic.png", auth=_AUTH)

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert atoms[0].kind == AtomKind.IMAGE
    assert atoms[0].format == "png"


async def test_binary_unrepresented_object_is_partial(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(data=b"\x00\x01\x02", content_type="application/zip", size=3)
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://bkt/archive.zip", auth=_AUTH)

    assert isinstance(result, Partial)
    assert any(g.kind == ErrorKind.UNSUPPORTED for g in result.gaps)


async def test_per_call_oauth_token_is_used_not_ambient(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(data=b"x", content_type="text/plain")
    _install(monkeypatch, blob, recorder)

    auth = OAuth2Auth(access_token="ya29.per-call")
    result = await GCSFetcher().fetch("gs://bkt/obj.txt", auth=auth)

    assert isinstance(result, Success)
    assert recorder["access_token"] == "ya29.per-call"
    assert recorder["bucket"] == "bkt"
    assert recorder["key"] == "obj.txt"


async def test_missing_object_is_not_found(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(raises=_GoogleError(404))
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://bkt/gone.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND
    assert result.locator == "gs://bkt/gone.txt"


async def test_denied_is_permission_denied(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(raises=_GoogleError(403))
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://bkt/secret.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED


async def test_bad_token_is_auth_failed(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(raises=_GoogleError(401))
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://bkt/obj.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_throttled_is_rate_limited(monkeypatch):
    recorder: dict[str, Any] = {}
    blob = _FakeBlob(raises=_GoogleError(429))
    _install(monkeypatch, blob, recorder)

    result = await GCSFetcher().fetch("gs://bkt/obj.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.RATE_LIMITED


async def test_missing_auth_is_auth_failed_without_client(monkeypatch):
    _install_tripwire(monkeypatch)

    result = await GCSFetcher().fetch("gs://bkt/obj.txt", auth=None)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_non_oauth_auth_is_auth_failed(monkeypatch):
    _install_tripwire(monkeypatch)

    result = await GCSFetcher().fetch("gs://bkt/obj.txt", auth=BearerAuth(token="nope"))

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_invalid_uri_is_invalid_input(monkeypatch):
    recorder: dict[str, Any] = {}
    _install(monkeypatch, _FakeBlob(), recorder)

    result = await GCSFetcher().fetch("gs://bucket-only", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_unsupported_when_extra_missing(monkeypatch):
    monkeypatch.setattr(gcs_module, "GCS_AVAILABLE", False)

    result = await GCSFetcher().fetch("gs://bkt/obj.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


def test_can_handle():
    assert GCSFetcher.can_handle("gs://bucket/key")
    assert not GCSFetcher.can_handle("s3://bucket/key")
    assert not GCSFetcher.can_handle("file:///tmp/x")
