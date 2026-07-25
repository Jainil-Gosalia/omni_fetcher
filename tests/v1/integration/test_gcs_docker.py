"""Real integration test for the v1 ``gcs`` connector against fake-gcs-server.

Drives ``GCSFetcher`` through its real ``google-cloud-storage`` path against the
``fsouza/fake-gcs-server`` emulator, selected via the standard
``STORAGE_EMULATOR_HOST`` environment variable, so the connector is unchanged.
Skipped unless the emulator is reachable at ``$STORAGE_EMULATOR_HOST`` (default
``http://localhost:4443``).

Spin one up with Docker:

    docker run -d --name omni-gcs -p 4443:4443 fsouza/fake-gcs-server -scheme http
"""

from __future__ import annotations

import os

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import OAuth2Auth
from omni_fetcher.v1.connectors.gcs import GCSFetcher
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

_HOST = os.environ.get("STORAGE_EMULATOR_HOST", "http://localhost:4443")
os.environ["STORAGE_EMULATOR_HOST"] = _HOST

pytest.importorskip("google.cloud.storage", reason="the 'gcs' extra is not installed")
from google.auth.credentials import AnonymousCredentials  # noqa: E402
from google.cloud import storage  # noqa: E402

_BUCKET = "omni-bucket"
_AUTH = OAuth2Auth(access_token="emulator-token")


@pytest.fixture
def obj():
    """Create a bucket + object in the emulator; skip if unreachable."""
    try:
        client = storage.Client(project="omni", credentials=AnonymousCredentials())
        try:
            bucket = client.create_bucket(_BUCKET)
        except Exception:
            bucket = client.bucket(_BUCKET)
        blob = bucket.blob("hello.txt")
        blob.upload_from_string("hello from gcs", content_type="text/plain")
    except Exception as exc:  # noqa: BLE001 - any failure = emulator not usable
        pytest.skip(f"fake-gcs-server not usable at {_HOST}: {exc}")
    yield


async def test_read_text_object(obj):
    result = await GCSFetcher().fetch(f"gs://{_BUCKET}/hello.txt", auth=_AUTH)

    assert isinstance(result, Success), result
    node = result.tree
    assert node.metadata.kind == "file"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "hello from gcs"
    extra = node.metadata.source_extra["gcs"]
    assert extra["bucket"] == _BUCKET
    assert extra["object"] == "hello.txt"


async def test_missing_object_is_not_found(obj):
    result = await GCSFetcher().fetch(f"gs://{_BUCKET}/nope.txt", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND
