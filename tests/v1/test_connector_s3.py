"""External-behaviour tests for the v1 ``s3`` connector.

These tests exercise only the public contract surface -- ``stream()`` /
``fetch()`` returning a canonical ``Result`` -- with boto3 fully stubbed. No
real AWS call is ever made: a fake client records the credentials it was
constructed with (so the per-call ``AwsAuth`` can be asserted) and replays a
scripted ``get_object`` response or raises a scripted ``ClientError``.

Covered:

- a text object becomes a ``Success`` with a canonical ``kind`` ``"file"`` node
  whose content lives in a ``Text`` atom and whose descriptive fields live in
  ``source_extra["s3"]``;
- a CSV object becomes a ``Table`` atom;
- ``AccessDenied`` -> ``error(PERMISSION_DENIED)``;
- a missing key (``NoSuchKey``) -> ``error(NOT_FOUND)``;
- bad credentials (``InvalidAccessKeyId``) -> ``error(AUTH_FAILED)``;
- the per-call ``AwsAuth`` is the credential used (not ambient env);
- a call with no ``AwsAuth`` is ``error(AUTH_FAILED)`` without touching boto3.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from botocore.exceptions import ClientError

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import AwsAuth, BearerAuth
from omni_fetcher.v1.connectors import s3 as s3_module
from omni_fetcher.v1.connectors.s3 import S3Fetcher
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success


class _FakeBody:
    """A minimal stand-in for a boto3 streaming body."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeClient:
    """A boto3-client stand-in that records its construction kwargs.

    ``get_object`` either returns a scripted response dict or raises a
    scripted exception, so every code path is driven without real AWS.
    """

    def __init__(
        self,
        *,
        response: Optional[dict[str, Any]] = None,
        raises: Optional[BaseException] = None,
        recorder: Optional[dict[str, Any]] = None,
    ) -> None:
        self._response = response
        self._raises = raises
        self._recorder = recorder
        self.calls: list[dict[str, Any]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append({"Bucket": Bucket, "Key": Key})
        if self._raises is not None:
            raise self._raises
        return dict(self._response or {})


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeClient,
    recorder: dict[str, Any],
) -> None:
    """Patch ``boto3.client`` in the connector to return ``client``.

    The recorder captures the kwargs boto3 was called with so a test can
    assert the per-call ``AwsAuth`` parts (not ambient creds) were used.
    """

    def _fake_client(service: str, **kwargs: Any) -> _FakeClient:
        recorder["service"] = service
        recorder["kwargs"] = kwargs
        return client

    monkeypatch.setattr(s3_module.boto3, "client", _fake_client)


def _client_error(code: str, status: int) -> ClientError:
    """Build a botocore ``ClientError`` with a given code and HTTP status."""
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "GetObject",
    )


_AUTH = AwsAuth(
    access_key_id="AKIA_TEST",
    secret_access_key="secret_test",
    region="eu-west-1",
)


async def test_text_object_is_canonical_success(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        response={
            "Body": _FakeBody(b"hello from s3"),
            "ContentType": "text/plain",
            "ContentLength": 13,
            "ETag": '"abc123"',
            "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
    )
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://my-bucket/notes.txt", auth=_AUTH)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "file"
    assert node.metadata.source_url == "s3://my-bucket/notes.txt"

    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "hello from s3"

    # Descriptive fields live in the namespaced source_extra, not on the atom.
    extra = node.metadata.source_extra["s3"]
    assert extra["bucket"] == "my-bucket"
    assert extra["key"] == "notes.txt"
    assert extra["size"] == 13
    assert extra["etag"] == '"abc123"'
    assert extra["content_type"] == "text/plain"
    assert extra["last_modified"] == "2026-01-02T00:00:00+00:00"


async def test_csv_object_becomes_table(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        response={
            "Body": _FakeBody(b"a,b\n1,2\n3,4\n"),
            "ContentType": "text/csv",
            "ContentLength": 12,
        }
    )
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://bkt/data.csv", auth=_AUTH)

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["a", "b"]
    assert atoms[0].rows == [["1", "2"], ["3", "4"]]


async def test_per_call_aws_auth_is_used_not_ambient(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(response={"Body": _FakeBody(b"x"), "ContentType": "text/plain"})
    _install_fake_client(monkeypatch, client, recorder)

    auth = AwsAuth(
        access_key_id="AKIA_PERCALL",
        secret_access_key="percall_secret",
        session_token="sess_token",
        region="ap-south-1",
    )
    result = await S3Fetcher().fetch("s3://bkt/obj.txt", auth=auth)

    assert isinstance(result, Success)
    # The boto3 client was constructed with exactly the per-call credential
    # parts -- never ambient/instance credentials.
    kwargs = recorder["kwargs"]
    assert recorder["service"] == "s3"
    assert kwargs["aws_access_key_id"] == "AKIA_PERCALL"
    assert kwargs["aws_secret_access_key"] == "percall_secret"
    assert kwargs["aws_session_token"] == "sess_token"
    assert kwargs["region_name"] == "ap-south-1"
    # The object was fetched from the parsed bucket/key.
    assert client.calls == [{"Bucket": "bkt", "Key": "obj.txt"}]


async def test_access_denied_is_permission_denied(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(raises=_client_error("AccessDenied", 403))
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://bkt/secret.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED
    assert result.locator == "s3://bkt/secret.txt"


async def test_missing_key_is_not_found(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(raises=_client_error("NoSuchKey", 404))
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://bkt/gone.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_bad_credentials_is_auth_failed(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(raises=_client_error("InvalidAccessKeyId", 403))
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://bkt/obj.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_missing_auth_is_auth_failed_without_boto(monkeypatch):
    # boto3.client is replaced with a tripwire: it must never be reached when
    # no AwsAuth is supplied.
    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("boto3 client must not be built without auth")

    monkeypatch.setattr(s3_module.boto3, "client", _explode)

    result = await S3Fetcher().fetch("s3://bkt/obj.txt", auth=None)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_non_aws_auth_is_auth_failed(monkeypatch):
    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("boto3 client must not be built for non-AWS auth")

    monkeypatch.setattr(s3_module.boto3, "client", _explode)

    result = await S3Fetcher().fetch("s3://bkt/obj.txt", auth=BearerAuth(token="nope"))

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_binary_unrepresented_object_is_partial(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        response={
            "Body": _FakeBody(b"\x00\x01\x02"),
            "ContentType": "application/zip",
            "ContentLength": 3,
        }
    )
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://bkt/archive.zip", auth=_AUTH)

    assert isinstance(result, Partial)
    assert any(g.kind == ErrorKind.UNSUPPORTED for g in result.gaps)


async def test_image_object_becomes_image_atom(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(
        response={
            "Body": _FakeBody(b"\x89PNG\r\n\x1a\n"),
            "ContentType": "image/png",
            "ContentLength": 8,
        }
    )
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://bkt/pic.png", auth=_AUTH)

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.IMAGE
    assert atoms[0].format == "png"


async def test_invalid_uri_is_invalid_input(monkeypatch):
    recorder: dict[str, Any] = {}
    client = _FakeClient(response={"Body": _FakeBody(b"")})
    _install_fake_client(monkeypatch, client, recorder)

    result = await S3Fetcher().fetch("s3://bucket-only", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


def test_can_handle():
    assert S3Fetcher.can_handle("s3://bucket/key")
    assert S3Fetcher.can_handle("https://b.s3.amazonaws.com/key")
    assert not S3Fetcher.can_handle("file:///tmp/x")
