"""Storage backend selection, introspection, and export to GCP."""

from __future__ import annotations

from pathlib import Path

from hive.blobstore import GcsBlobStore, LocalBlobStore
from hive.config import Config
from hive.models import DEFAULT_WORKSPACE_ID, Workspace
from hive.store import (
    FileStore,
    FirestoreStore,
    MemoryStore,
    StoreBase,
    _COLLECTIONS,
)


def make_store(config: Config) -> StoreBase:
    if config.gcp_project:
        return FirestoreStore(config.gcp_project)
    return FileStore(config.data_dir / "store")


def make_blob_store(config: Config):
    if config.gcs_bucket:
        return GcsBlobStore(config.gcs_bucket)
    return LocalBlobStore(config.data_dir / "blobs")


def storage_info(store: StoreBase, config: Config, blobs) -> dict:
    if isinstance(store, FirestoreStore):
        backend = "firestore"
        store_path = None
        export_available = False
    elif isinstance(store, FileStore):
        backend = "file"
        store_path = str(store.root)
        export_available = True
    else:
        backend = "memory"
        store_path = None
        export_available = False

    if isinstance(blobs, GcsBlobStore):
        blob_backend = "gcs"
        blob_path = None
    elif isinstance(blobs, LocalBlobStore):
        blob_backend = "local"
        blob_path = str(blobs.root)
    else:
        blob_backend = "local"
        blob_path = str(config.data_dir / "blobs") if config.data_dir else None

    counts: dict[str, int] = {}
    if backend == "file":
        for model, name in _COLLECTIONS.items():
            counts[name] = len(store.list(model))

    return {
        "backend": backend,
        "store_path": store_path,
        "gcp_project": config.gcp_project or None,
        "blob_backend": blob_backend,
        "blob_path": blob_path,
        "gcs_bucket": config.gcs_bucket or None,
        "counts": counts,
        "export_available": export_available,
    }


def copy_store(source: StoreBase, dest: StoreBase) -> dict[str, int]:
    """Copy every document and org context from ``source`` to ``dest``."""
    counts: dict[str, int] = {}
    for model, collection in _COLLECTIONS.items():
        items = source.list(model)
        for item in items:
            dest.put(item)
        counts[collection] = len(items)

    workspace_ids = {ws.id for ws in source.list(Workspace)}
    workspace_ids.add(DEFAULT_WORKSPACE_ID)
    for workspace_id in workspace_ids:
        text = source.get_org_context(workspace_id)
        if text:
            dest.set_org_context(text, workspace_id)
    return counts


def copy_blobs(source: LocalBlobStore, dest: GcsBlobStore) -> int:
    if not source.root.is_dir():
        return 0
    copied = 0
    for path in source.root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(source.root).as_posix()
            dest.put(rel, path.read_bytes())
            copied += 1
    return copied


def export_to_gcp(
    store: FileStore,
    blobs: LocalBlobStore,
    *,
    gcp_project: str,
    gcs_bucket: str = "",
) -> dict:
    dest_store = FirestoreStore(gcp_project)
    counts = copy_store(store, dest_store)
    blob_count = 0
    if gcs_bucket:
        blob_count = copy_blobs(blobs, GcsBlobStore(gcs_bucket))
    return {
        "gcp_project": gcp_project,
        "gcs_bucket": gcs_bucket or None,
        "documents": counts,
        "blobs": blob_count,
        "message": (
            f"Exported to Firestore project {gcp_project!r}. "
            f"Restart with HIVE_GCP_PROJECT={gcp_project!r} "
            + (f"and HIVE_GCS_BUCKET={gcs_bucket!r} " if gcs_bucket else "")
            + "to use the cloud store."
        ),
    }
