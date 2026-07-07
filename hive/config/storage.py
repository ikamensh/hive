"""Storage backend selection, introspection, and local-to-managed migration."""

from __future__ import annotations

import uuid
import time

from hive.persistence import (
    CachedStore,
    FileStore,
    FirestoreStore,
    GcsBlobStore,
    LocalBlobStore,
    StoreBase,
    collection_name,
)
from hive.config.settings import Config
from hive.models import ALL_MODELS, DEFAULT_WORKSPACE_ID, Workspace


class ManagedStateConfigError(RuntimeError):
    """Raised when runtime storage is not configured for managed persistence."""


def managed_state_missing(config: Config) -> list[str]:
    missing: list[str] = []
    if not config.gcp_project.strip():
        missing.append("HIVE_GCP_PROJECT")
    if not config.gcs_bucket.strip():
        missing.append("HIVE_GCS_BUCKET")
    return missing


def managed_state_error(config: Config) -> ManagedStateConfigError:
    missing = managed_state_missing(config)
    lines = [
        "Hive requires managed state for runtime.",
        "Set:",
        "  HIVE_GCP_PROJECT=<gcp-project>",
        "  HIVE_GCS_BUCKET=<gcs-bucket>",
        "",
        "Then run:",
        "  gcloud auth application-default login",
        "  uv run hive run",
    ]
    if missing:
        lines.insert(1, f"Missing: {', '.join(missing)}")
    return ManagedStateConfigError("\n".join(lines))


def make_store(config: Config) -> StoreBase:
    if not config.gcp_project.strip():
        raise managed_state_error(config)
    # The chief is the single writer (leader lease), so it reads from memory
    # and writes through — Firestore charges per document read otherwise.
    return CachedStore(FirestoreStore(config.gcp_project.strip()))


def make_blob_store(config: Config):
    if not config.gcs_bucket.strip():
        raise managed_state_error(config)
    return GcsBlobStore(config.gcs_bucket.strip())


def storage_info(store: StoreBase, config: Config, blobs) -> dict:
    if isinstance(store, CachedStore):
        store = store.inner  # report on the backing store, not the cache
    if isinstance(store, FirestoreStore):
        backend = "firestore"
        store_path = None
        export_available = False
    elif isinstance(store, FileStore):
        backend = "file"
        store_path = str(store.root)
        export_available = False
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
        for model in ALL_MODELS:
            counts[collection_name(model)] = len(store.list(model))

    fully_managed = backend == "firestore" and blob_backend == "gcs"
    return {
        "backend": backend,
        "store_path": store_path,
        "gcp_project": config.gcp_project or None,
        "blob_backend": blob_backend,
        "blob_path": blob_path,
        "gcs_bucket": config.gcs_bucket or None,
        "counts": counts,
        "export_available": export_available,
        "fully_managed": fully_managed,
    }


def copy_store(source: StoreBase, dest: StoreBase) -> dict[str, int]:
    """Copy every document and org context from ``source`` to ``dest``."""
    counts: dict[str, int] = {}
    for model in ALL_MODELS:
        items = source.list(model)
        for item in items:
            dest.put(item)
        counts[collection_name(model)] = len(items)

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


def _verify_store_copy(source: StoreBase, dest: StoreBase) -> None:
    for model in ALL_MODELS:
        for item in source.list(model):
            copied = dest.get(model, item.id)
            if copied is None:
                raise RuntimeError(
                    f"migration verification failed: missing {model.__name__} {item.id}"
                )
            if copied.model_dump() != item.model_dump():
                raise RuntimeError(
                    f"migration verification failed: changed {model.__name__} {item.id}"
                )

    workspace_ids = {ws.id for ws in source.list(Workspace)}
    workspace_ids.add(DEFAULT_WORKSPACE_ID)
    for workspace_id in workspace_ids:
        text = source.get_org_context(workspace_id)
        if text and dest.get_org_context(workspace_id) != text:
            raise RuntimeError(f"migration verification failed: org context for {workspace_id}")


def _verify_blob_copy(source: LocalBlobStore, dest: GcsBlobStore) -> int:
    if not source.root.is_dir():
        return 0
    verified = 0
    for path in source.root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(source.root).as_posix()
            data = path.read_bytes()
            if dest.get(rel) != data:
                raise RuntimeError(f"migration verification failed: blob {rel}")
            verified += 1
    return verified


def _live_leader(store: StoreBase, workspace_id: str) -> dict | None:
    db = getattr(store, "_db", None)
    if db is None:
        return None
    snap = (
        db.collection("workspaces")
        .document(workspace_id)
        .collection("settings")
        .document("leader_lease")
        .get()
    )
    if not snap.exists:
        return None
    lease = snap.to_dict()
    if lease and lease.get("expires", 0) > time.time():
        return lease
    return None


def migrate_local_state(
    store: FileStore,
    blobs: LocalBlobStore,
    *,
    gcp_project: str,
    gcs_bucket: str,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    verify: bool = True,
) -> dict:
    if not gcp_project.strip():
        raise ValueError("gcp_project is required")
    if not gcs_bucket.strip():
        raise ValueError("gcs_bucket is required")
    dest_store = FirestoreStore(gcp_project.strip())
    if leader := _live_leader(dest_store, workspace_id):
        holder = leader.get("holder", "unknown")
        raise RuntimeError(
            f"refusing migration: chief {holder!r} holds the leader lease "
            f"for workspace {workspace_id!r}"
        )
    dest_blobs = GcsBlobStore(gcs_bucket.strip())
    counts = copy_store(store, dest_store)
    blob_count = copy_blobs(blobs, dest_blobs)
    verified_blobs = 0
    if verify:
        _verify_store_copy(store, dest_store)
        verified_blobs = _verify_blob_copy(blobs, dest_blobs)
    return {
        "gcp_project": gcp_project.strip(),
        "gcs_bucket": gcs_bucket.strip(),
        "workspace_id": workspace_id,
        "documents": counts,
        "blobs": blob_count,
        "verified": verify,
        "verified_blobs": verified_blobs,
        "message": (
            f"Migrated local state to Firestore project {gcp_project.strip()!r} and "
            f"GCS bucket {gcs_bucket.strip()!r}. Restart with "
            f"HIVE_GCP_PROJECT={gcp_project.strip()!r} and "
            f"HIVE_GCS_BUCKET={gcs_bucket.strip()!r}."
        ),
    }


def managed_state_doctor(config: Config) -> dict:
    """Exercise Firestore + GCS without starting the chief."""
    missing = managed_state_missing(config)
    checks: list[dict] = [
        {"name": "HIVE_GCP_PROJECT", "ok": "HIVE_GCP_PROJECT" not in missing},
        {"name": "HIVE_GCS_BUCKET", "ok": "HIVE_GCS_BUCKET" not in missing},
        {"name": "HIVE_RUNNER_TOKEN", "ok": bool(config.runner_token.strip())},
    ]
    if missing:
        return {"ok": False, "checks": checks, "error": str(managed_state_error(config))}

    try:
        store = FirestoreStore(config.gcp_project.strip())
        checks.append({"name": "firestore_client", "ok": True})
    except Exception as exc:
        checks.append({"name": "firestore_client", "ok": False, "detail": str(exc)})
        store = None
    try:
        bucket = GcsBlobStore(config.gcs_bucket.strip())
        checks.append({"name": "gcs_client", "ok": True})
    except Exception as exc:
        checks.append({"name": "gcs_client", "ok": False, "detail": str(exc)})
        bucket = None
    marker = uuid.uuid4().hex
    if store is not None:
        firestore_ref = (
            store._db.collection("workspaces")
            .document(config.workspace_id)
            .collection("settings")
            .document(f"doctor_{marker}")
        )
        try:
            firestore_ref.set({"marker": marker})
            snap = firestore_ref.get()
            checks.append(
                {
                    "name": "firestore_read_write",
                    "ok": snap.exists and snap.to_dict().get("marker") == marker,
                }
            )
        except Exception as exc:
            checks.append({"name": "firestore_read_write", "ok": False, "detail": str(exc)})
        finally:
            try:
                firestore_ref.delete()
            except Exception:
                pass
        try:
            workspace = store.get(Workspace, config.workspace_id)
            if workspace is None:
                workspace = store.put(
                    Workspace(
                        id=config.workspace_id,
                        name=config.workspace_name or "personal",
                    )
                )
            checks.append(
                {
                    "name": "workspace_bootstrap",
                    "ok": workspace.id == config.workspace_id,
                }
            )
        except Exception as exc:
            checks.append({"name": "workspace_bootstrap", "ok": False, "detail": str(exc)})

    blob_key = f"workspaces/{config.workspace_id}/doctor/{marker}.txt"
    data = f"hive doctor {marker}".encode()
    if bucket is not None:
        try:
            bucket.put(blob_key, data)
            checks.append({"name": "gcs_read_write", "ok": bucket.get(blob_key) == data})
        except Exception as exc:
            checks.append({"name": "gcs_read_write", "ok": False, "detail": str(exc)})
        finally:
            try:
                bucket.delete(blob_key)
            except Exception:
                pass

    leader_data = None
    if store is not None:
        try:
            leader = (
                store._db.collection("workspaces")
                .document(config.workspace_id)
                .collection("settings")
                .document("leader_lease")
                .get()
            )
            leader_data = leader.to_dict() if leader.exists else None
        except Exception as exc:
            checks.append({"name": "leader_lease_read", "ok": False, "detail": str(exc)})
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "gcp_project": config.gcp_project.strip(),
        "gcs_bucket": config.gcs_bucket.strip(),
        "workspace_id": config.workspace_id,
        "leader": leader_data,
    }
