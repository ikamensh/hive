"""Typed document stores over Pydantic models — no fixed schema, bring your own.

A document is any Pydantic model with a string ``id`` field; its collection
name derives from the class name (``Task`` → ``tasks``), overridable with a
``__collection__`` class attribute. `StoreBase` is the contract; `MemoryStore`
(tests), `FileStore` (JSON files on disk), and `FirestoreStore` (GCP) are
independent implementations. `CachedStore` is a write-through in-memory cache
over any of them for a single-writer process.

`update` is the atomic read-modify-write: the way to mutate a document without
clobbering a concurrent writer. Plain `put` is last-write-wins and fine for
freshly-built objects and uncontended writes. Documents sort by their
``created_at`` field when they have one, so `list(..., limit=N)` returns the
most recent N (still oldest→newest).

Beyond documents, a store carries two singletons per scope, for coordinating
the processes that share it: a free-text context blob (`get/set_org_context`)
and a TTL leader lease (`claim/release_leader`) that fences out a second
writer. Scopes ("workspaces") let one physical store serve several tenants;
everything defaults to the ``"default"`` scope.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

DEFAULT_SCOPE = "default"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def collection_name(model: type[BaseModel]) -> str:
    """``Task`` → ``tasks``, ``HumanTask`` → ``human_tasks``. Models with
    irregular plurals declare ``__collection__`` themselves."""
    declared = getattr(model, "__collection__", None)
    if declared:
        return declared
    return _CAMEL_BOUNDARY.sub("_", model.__name__).lower() + "s"


def _created_at(obj) -> float:
    return getattr(obj, "created_at", 0.0)


_NO_DEFAULT = object()


def _field_default(model: type[BaseModel], key: str):
    """The model's declared static default for `key`, or _NO_DEFAULT. Dynamic
    defaults (factories: ids, timestamps) never count — they differ per
    instance, so a missing key cannot be assumed to hold one."""
    field = model.model_fields.get(key)
    if field is None or field.default_factory is not None:
        return _NO_DEFAULT
    return _NO_DEFAULT if field.default is PydanticUndefined else field.default


def _matches(raw: dict, key: str, value, default) -> bool:
    """Equality filter over a serialized doc. A doc written before `key`
    existed matches when the filter asks for the model's declared default —
    old rows behave as if migrated."""
    if key not in raw:
        return default is not _NO_DEFAULT and default == value
    return raw.get(key) == value


class StoreBase(ABC):
    """The persistence contract every caller depends on."""

    @abstractmethod
    def put(self, obj: M) -> M: ...

    @abstractmethod
    def get(self, model: type[M], id: str) -> M | None: ...

    @abstractmethod
    def list(self, model: type[M], *, limit: int | None = None, **filters) -> list[M]:
        """Equality-filtered, oldest→newest. With `limit`, the most recent
        `limit` documents (still returned oldest→newest)."""

    @abstractmethod
    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        """Atomically fetch, apply `mutate(obj)`, persist, and return the
        updated object — or None if it doesn't exist. Safe against concurrent
        writers; `mutate` may be retried, so it must be side-effect free."""

    @abstractmethod
    def delete(self, model: type[M], id: str) -> None: ...

    @abstractmethod
    def raw_docs(self, collection: str) -> list[dict]:
        """Every serialized document of `collection`, unvalidated. Exists so
        `CachedStore` can hydrate without forcing old rows through current
        pydantic schemas (validation stays lazy, at get/list time)."""

    @abstractmethod
    def get_org_context(self, workspace_id: str = DEFAULT_SCOPE) -> str: ...

    @abstractmethod
    def set_org_context(self, text: str, workspace_id: str = DEFAULT_SCOPE) -> None: ...

    @abstractmethod
    def claim_leader(self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_SCOPE) -> str: ...

    @abstractmethod
    def release_leader(self, holder: str, workspace_id: str = DEFAULT_SCOPE) -> bool:
        """Release the leader lease iff `holder` still owns it."""


class MemoryStore(StoreBase):
    """In-process store for tests and dev. An RLock makes every operation
    thread-safe, so `update` is genuinely atomic across threads."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = defaultdict(dict)
        self.org_context: str = ""
        self._org_contexts: dict[str, str] = {}
        self._lease: dict | None = None
        self._leases: dict[str, dict] = {}
        self._lock = threading.RLock()

    def _collection(self, collection: str) -> dict[str, dict]:
        """All access to `_data` goes through here (hydration hook for
        `CachedStore`). Callers hold `_lock`."""
        return self._data[collection]

    def put(self, obj: M) -> M:
        with self._lock:
            self._collection(collection_name(type(obj)))[obj.id] = obj.model_dump()
        return obj

    def get(self, model: type[M], id: str) -> M | None:
        with self._lock:
            raw = self._collection(collection_name(model)).get(id)
        return model.model_validate(raw) if raw else None

    def list(self, model: type[M], *, limit: int | None = None, **filters) -> list[M]:
        with self._lock:
            rows = list(self._collection(collection_name(model)).values())
        defaults = {k: _field_default(model, k) for k in filters}
        out = [
            model.model_validate(raw)
            for raw in rows
            if all(_matches(raw, k, v, defaults[k]) for k, v in filters.items())
        ]
        out.sort(key=_created_at)
        return out[-limit:] if limit is not None else out

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        with self._lock:
            collection = self._collection(collection_name(model))
            raw = collection.get(id)
            if raw is None:
                return None
            obj = model.model_validate(raw)
            mutate(obj)
            collection[obj.id] = obj.model_dump()
            return obj

    def delete(self, model: type[M], id: str) -> None:
        with self._lock:
            self._collection(collection_name(model)).pop(id, None)

    def raw_docs(self, collection: str) -> list[dict]:
        with self._lock:
            return [dict(raw) for raw in self._collection(collection).values()]

    def get_org_context(self, workspace_id: str = DEFAULT_SCOPE) -> str:
        with self._lock:
            if workspace_id == DEFAULT_SCOPE:
                return self.org_context
            return self._org_contexts.get(workspace_id, "")

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_SCOPE) -> None:
        with self._lock:
            if workspace_id == DEFAULT_SCOPE:
                self.org_context = text
            else:
                self._org_contexts[workspace_id] = text

    def claim_leader(self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_SCOPE) -> str:
        """Claim or renew the single-writer lease. Returns the holder
        that owns the lease after this attempt; callers losing the claim see
        the competing holder's name. A lease is free once its TTL lapses, so
        a crashed leader is superseded within ttl_s."""
        with self._lock:
            lease = self._lease if workspace_id == DEFAULT_SCOPE else self._leases.get(workspace_id)
            if lease and lease["holder"] != holder and lease["expires"] > time.time():
                return lease["holder"]
            lease = {"holder": holder, "expires": time.time() + ttl_s}
            if workspace_id == DEFAULT_SCOPE:
                self._lease = lease
            else:
                self._leases[workspace_id] = lease
            return holder

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_SCOPE) -> bool:
        with self._lock:
            if workspace_id == DEFAULT_SCOPE:
                lease = self._lease
                if not lease or lease["holder"] != holder:
                    return False
                self._lease = None
                return True
            lease = self._leases.get(workspace_id)
            if not lease or lease["holder"] != holder:
                return False
            self._leases.pop(workspace_id)
            return True


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data, separators=(",", ":")))
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


def _read_json_file(path: Path, *, strict: bool) -> dict | None:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError(f"Corrupt store file {path}: {exc}") from exc
        log.warning("Skipping corrupt store file %s: %s", path, exc)
        return None


class FileStore(MemoryStore):
    """JSON-on-disk store for local runs. Same in-process locking as
    MemoryStore, but every mutation is flushed to ``root/<collection>/<id>.json``.
    Collections are whatever directories exist — no schema registration."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        super().__init__()
        self._load()

    def _collection_dir(self, collection: str) -> Path:
        return self.root / collection

    def _doc_path(self, collection: str, doc_id: str) -> Path:
        return self._collection_dir(collection) / f"{doc_id}.json"

    def _settings_path(self, name: str) -> Path:
        return self.root / "settings" / name

    def _load(self) -> None:
        if self.root.is_dir():
            for directory in self.root.iterdir():
                if not directory.is_dir() or directory.name == "settings":
                    continue
                for path in directory.glob("*.json"):
                    raw = _read_json_file(path, strict=False)
                    if raw is None:
                        continue
                    self._data[directory.name][raw["id"]] = raw

        settings = self.root / "settings"
        if not settings.is_dir():
            return
        org_path = settings / "org_context.json"
        if org_path.is_file():
            raw = _read_json_file(org_path, strict=True)
            self.org_context = raw.get("text", "")
        for path in settings.glob("org_context_*.json"):
            workspace_id = path.stem.removeprefix("org_context_")
            raw = _read_json_file(path, strict=True)
            self._org_contexts[workspace_id] = raw.get("text", "")
        lease_path = settings / "leader_lease.json"
        if lease_path.is_file():
            self._lease = _read_json_file(lease_path, strict=True)
        for path in settings.glob("leader_lease_*.json"):
            workspace_id = path.stem.removeprefix("leader_lease_")
            self._leases[workspace_id] = _read_json_file(path, strict=True)

    def _persist_doc(self, collection: str, doc_id: str, raw: dict) -> None:
        _atomic_write_json(self._doc_path(collection, doc_id), raw)

    def _delete_doc(self, collection: str, doc_id: str) -> None:
        self._doc_path(collection, doc_id).unlink(missing_ok=True)

    def _persist_org_context(self, workspace_id: str, text: str) -> None:
        if workspace_id == DEFAULT_SCOPE:
            _atomic_write_json(self._settings_path("org_context.json"), {"text": text})
        else:
            _atomic_write_json(
                self._settings_path(f"org_context_{workspace_id}.json"),
                {"text": text},
            )

    def _persist_lease(self, workspace_id: str, lease: dict) -> None:
        if workspace_id == DEFAULT_SCOPE:
            _atomic_write_json(self._settings_path("leader_lease.json"), lease)
        else:
            _atomic_write_json(self._settings_path(f"leader_lease_{workspace_id}.json"), lease)

    def _delete_lease(self, workspace_id: str) -> None:
        if workspace_id == DEFAULT_SCOPE:
            self._settings_path("leader_lease.json").unlink(missing_ok=True)
        else:
            self._settings_path(f"leader_lease_{workspace_id}.json").unlink(missing_ok=True)

    def put(self, obj: M) -> M:
        with self._lock:
            collection = collection_name(type(obj))
            self._collection(collection)[obj.id] = obj.model_dump()
            self._persist_doc(collection, obj.id, self._collection(collection)[obj.id])
        return obj

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        with self._lock:
            collection = self._collection(collection_name(model))
            raw = collection.get(id)
            if raw is None:
                return None
            obj = model.model_validate(raw)
            mutate(obj)
            collection[obj.id] = obj.model_dump()
            self._persist_doc(collection_name(model), id, collection[id])
            return obj

    def delete(self, model: type[M], id: str) -> None:
        with self._lock:
            collection = collection_name(model)
            self._collection(collection).pop(id, None)
            self._delete_doc(collection, id)

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_SCOPE) -> None:
        with self._lock:
            if workspace_id == DEFAULT_SCOPE:
                self.org_context = text
            else:
                self._org_contexts[workspace_id] = text
            self._persist_org_context(workspace_id, text)

    def claim_leader(self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_SCOPE) -> str:
        with self._lock:
            lease = self._lease if workspace_id == DEFAULT_SCOPE else self._leases.get(workspace_id)
            if lease and lease["holder"] != holder and lease["expires"] > time.time():
                return lease["holder"]
            lease = {"holder": holder, "expires": time.time() + ttl_s}
            if workspace_id == DEFAULT_SCOPE:
                self._lease = lease
            else:
                self._leases[workspace_id] = lease
            self._persist_lease(workspace_id, lease)
            return holder

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_SCOPE) -> bool:
        with self._lock:
            if workspace_id == DEFAULT_SCOPE:
                lease = self._lease
                if not lease or lease["holder"] != holder:
                    return False
                self._lease = None
            else:
                lease = self._leases.get(workspace_id)
                if not lease or lease["holder"] != holder:
                    return False
                self._leases.pop(workspace_id)
            self._delete_lease(workspace_id)
            return True


class FirestoreStore(StoreBase):
    def __init__(self, project: str, database: str = "(default)") -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project, database=database)

    def put(self, obj: M) -> M:
        self._db.collection(collection_name(type(obj))).document(obj.id).set(obj.model_dump())
        return obj

    def get(self, model: type[M], id: str) -> M | None:
        snap = self._db.collection(collection_name(model)).document(id).get()
        return model.model_validate(snap.to_dict()) if snap.exists else None

    def list(self, model: type[M], *, limit: int | None = None, **filters) -> list[M]:
        from google.cloud.firestore_v1 import FieldFilter

        query = self._db.collection(collection_name(model))
        defaults = {k: _field_default(model, k) for k in filters}
        for k, v in filters.items():
            if defaults[k] == v:
                continue  # docs written before `k` existed can't match a where()
            query = query.where(filter=FieldFilter(k, "==", v))
        out = []
        for snap in query.stream():
            raw = snap.to_dict()
            if all(_matches(raw, k, v, defaults[k]) for k, v in filters.items()):
                out.append(model.model_validate(raw))
        out.sort(key=_created_at)
        return out[-limit:] if limit is not None else out

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        from google.cloud import firestore

        ref = self._db.collection(collection_name(model)).document(id)
        transaction = self._db.transaction()

        @firestore.transactional
        def attempt(txn) -> M | None:
            snap = ref.get(transaction=txn)
            if not snap.exists:
                return None
            obj = model.model_validate(snap.to_dict())
            mutate(obj)
            txn.set(ref, obj.model_dump())
            return obj

        return attempt(transaction)

    def delete(self, model: type[M], id: str) -> None:
        self._db.collection(collection_name(model)).document(id).delete()

    def raw_docs(self, collection: str) -> list[dict]:
        return [snap.to_dict() for snap in self._db.collection(collection).stream()]

    def get_org_context(self, workspace_id: str = DEFAULT_SCOPE) -> str:
        snap = (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("settings")
            .document("org_context")
            .get()
        )
        if not snap.exists and workspace_id == DEFAULT_SCOPE:
            snap = self._db.collection("settings").document("org_context").get()
        return snap.to_dict().get("text", "") if snap.exists else ""

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_SCOPE) -> None:
        (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("settings")
            .document("org_context")
            .set({"text": text})
        )

    def claim_leader(self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_SCOPE) -> str:
        from google.cloud import firestore

        ref = (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("settings")
            .document("leader_lease")
        )
        transaction = self._db.transaction()

        @firestore.transactional
        def attempt(txn) -> str:
            snap = ref.get(transaction=txn)
            lease = snap.to_dict() if snap.exists else None
            if lease and lease["holder"] != holder and lease["expires"] > time.time():
                return lease["holder"]
            txn.set(ref, {"holder": holder, "expires": time.time() + ttl_s})
            return holder

        return attempt(transaction)

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_SCOPE) -> bool:
        from google.cloud import firestore

        ref = (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("settings")
            .document("leader_lease")
        )
        transaction = self._db.transaction()

        @firestore.transactional
        def attempt(txn) -> bool:
            snap = ref.get(transaction=txn)
            lease = snap.to_dict() if snap.exists else None
            if not lease or lease.get("holder") != holder:
                return False
            txn.delete(ref)
            return True

        return attempt(transaction)


class CachedStore(MemoryStore):
    """Write-through in-memory cache over a backing store, for a single-writer
    process.

    Correct only when this process is the sole writer — which is exactly what
    the leader lease guarantees (hive's chief holds it; every other party
    mutates state through the chief's API). All reads come from process
    memory; only writes reach the backing store. Without this, hive's
    supervisor tick and runner poll/heartbeat loops re-scan Firestore around
    the clock (~1.5M document reads per idle day, measured).

    Each collection hydrates from the backing store on first touch. Mutations
    persist to the backing store *first*, then commit to memory, so a backend
    failure surfaces as an exception while memory still mirrors durable state.
    Leases and org context pass through uncached: the lease is the
    cross-process fencing primitive and must stay authoritative in the
    backing store.
    """

    def __init__(self, inner: StoreBase) -> None:
        super().__init__()
        self.inner = inner
        self._hydrated: set[str] = set()

    def _collection(self, collection: str) -> dict[str, dict]:
        if collection not in self._hydrated:
            self._hydrated.add(collection)
            for raw in self.inner.raw_docs(collection):
                self._data[collection][raw["id"]] = raw
        return self._data[collection]

    def put(self, obj: M) -> M:
        with self._lock:
            self._collection(collection_name(type(obj)))  # hydrate before overlaying
            self.inner.put(obj)
            return super().put(obj)

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        # The in-process lock is the atomicity guarantee (single writer);
        # the backing store's own concurrent-writer protection is not needed.
        with self._lock:
            collection = self._collection(collection_name(model))
            raw = collection.get(id)
            if raw is None:
                return None
            obj = model.model_validate(raw)
            mutate(obj)
            self.inner.put(obj)
            collection[obj.id] = obj.model_dump()
            return obj

    def delete(self, model: type[M], id: str) -> None:
        with self._lock:
            self._collection(collection_name(model))  # hydrate so memory stays a mirror
            self.inner.delete(model, id)
            super().delete(model, id)

    def get_org_context(self, workspace_id: str = DEFAULT_SCOPE) -> str:
        return self.inner.get_org_context(workspace_id)

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_SCOPE) -> None:
        self.inner.set_org_context(text, workspace_id)

    def claim_leader(self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_SCOPE) -> str:
        return self.inner.claim_leader(holder, ttl_s, workspace_id)

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_SCOPE) -> bool:
        return self.inner.release_leader(holder, workspace_id)
