"""Storage introspection, managed-state selection, and migration helpers."""

import pytest

from hive.persistence.blobstore import LocalBlobStore
from hive.config.settings import Config
from hive.models import Project
from hive.config.storage import (
    ManagedStateConfigError,
    managed_state_missing,
    make_blob_store,
    make_store,
    migrate_local_state,
    storage_info,
)
from hive.persistence.store import CachedStore, FileStore, MemoryStore


class FakeFirestoreStore(MemoryStore):
    def __init__(self, project: str):
        super().__init__()
        self.project = project


class FakeGcsBlobStore:
    def __init__(self, bucket: str):
        self.bucket_name = bucket
        self.data: dict[str, bytes] = {}

    def get(self, name: str) -> bytes | None:
        return self.data.get(name)

    def put(self, name: str, data: bytes) -> None:
        self.data[name] = data

    def delete(self, name: str) -> None:
        self.data.pop(name, None)


def config(tmp_path, *, gcp_project="", gcs_bucket="") -> Config:
    return Config(
        gcp_project=gcp_project,
        gcs_bucket=gcs_bucket,
        gh_token="",
        gemini_api_key="",
        orch_model="",
        runner_token="test-token",
        data_dir=tmp_path,
    )


def test_runtime_storage_requires_firestore_and_gcs(tmp_path):
    cfg = config(tmp_path)

    assert managed_state_missing(cfg) == ["HIVE_GCP_PROJECT", "HIVE_GCS_BUCKET"]
    with pytest.raises(ManagedStateConfigError):
        make_store(cfg)
    with pytest.raises(ManagedStateConfigError):
        make_blob_store(cfg)


def test_production_app_refuses_unmanaged_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HIVE_GCP_PROJECT", raising=False)
    monkeypatch.delenv("HIVE_GCS_BUCKET", raising=False)
    from hive.api import production_app

    with pytest.raises(ManagedStateConfigError):
        production_app()


def test_runtime_storage_uses_managed_backends(tmp_path, monkeypatch):
    monkeypatch.setattr("hive.config.storage.FirestoreStore", FakeFirestoreStore)
    monkeypatch.setattr("hive.config.storage.GcsBlobStore", FakeGcsBlobStore)
    cfg = config(tmp_path, gcp_project="proj", gcs_bucket="bucket")

    store = make_store(cfg)
    blobs = make_blob_store(cfg)

    # The chief gets a write-through cache; the backing store is Firestore.
    assert isinstance(store, CachedStore)
    assert isinstance(store.inner, FakeFirestoreStore)
    assert store.inner.project == "proj"
    assert isinstance(blobs, FakeGcsBlobStore)
    assert blobs.bucket_name == "bucket"


def test_storage_info_for_direct_file_store(tmp_path):
    cfg = config(tmp_path)
    store = FileStore(tmp_path / "store")
    store.put(Project(name="p"))
    info = storage_info(store, cfg, LocalBlobStore(tmp_path / "blobs"))

    assert info["backend"] == "file"
    assert info["export_available"] is False
    assert info["fully_managed"] is False
    assert info["counts"]["projects"] == 1
    assert str(tmp_path / "store") in info["store_path"]


def test_migrate_local_state_requires_gcs_bucket(tmp_path):
    with pytest.raises(ValueError):
        migrate_local_state(
            FileStore(tmp_path / "store"),
            LocalBlobStore(tmp_path / "blobs"),
            gcp_project="proj",
            gcs_bucket="",
        )


def test_migrate_local_state_copies_and_verifies(tmp_path, monkeypatch):
    fake_store = FakeFirestoreStore("proj")
    fake_blobs = FakeGcsBlobStore("bucket")
    monkeypatch.setattr("hive.config.storage.FirestoreStore", lambda project: fake_store)
    monkeypatch.setattr("hive.config.storage.GcsBlobStore", lambda bucket: fake_blobs)

    source = FileStore(tmp_path / "store")
    project = source.put(Project(name="p"))
    source.set_org_context("ship daily")
    local_blobs = LocalBlobStore(tmp_path / "blobs")
    local_blobs.put("traces/t.jsonl", b'{"ok":true}\n')

    result = migrate_local_state(
        source,
        local_blobs,
        gcp_project="proj",
        gcs_bucket="bucket",
    )

    assert result["documents"]["projects"] == 1
    assert result["blobs"] == 1
    assert result["verified"] is True
    assert result["verified_blobs"] == 1
    assert fake_store.get(Project, project.id).name == "p"
    assert fake_store.get_org_context() == "ship daily"
    assert fake_blobs.get("traces/t.jsonl") == b'{"ok":true}\n'
