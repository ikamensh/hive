"""Storage introspection helpers."""

from hive.config import Config
from hive.models import Project
from hive.storage import make_blob_store, make_store, storage_info
from hive.store import FileStore


def test_make_store_uses_file_store_without_gcp(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path))
    config = Config.from_env()
    store = make_store(config)
    assert isinstance(store, FileStore)
    assert store.root == tmp_path / "store"


def test_storage_info_for_file_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path))
    config = Config.from_env()
    store = make_store(config)
    store.put(Project(name="p"))
    info = storage_info(store, config, make_blob_store(config))
    assert info["backend"] == "file"
    assert info["export_available"] is True
    assert info["counts"]["projects"] == 1
    assert str(tmp_path / "store") in info["store_path"]
