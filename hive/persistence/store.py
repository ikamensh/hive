"""Persistence. `StoreBase` is the contract; `MemoryStore` (tests), `FileStore`
(local dev — JSON files under HIVE_DATA_DIR), and `FirestoreStore` (prod) are
independent implementations of it. The chief wraps its store in `CachedStore`
so the control loops read from memory and only writes reach the backend.

Documents are pydantic models serialized to dicts. `update` is the atomic
read-modify-write: the way to mutate a document without clobbering a concurrent
writer (the supervisor loop and the request threadpool both touch tasks). Plain
`put` is last-write-wins and fine for freshly-built objects and uncontended writes.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, TypeVar

from hive.models import (
    AgentConversation,
    Checkout,
    DEFAULT_WORKSPACE_ID,
    Directive,
    Feedback,
    Finding,
    HumanTask,
    IssueRun,
    Machine,
    OrchestratorRun,
    Project,
    ProjectWorkstream,
    Question,
    QuestionStatus,
    Resource,
    Runner,
    Story,
    Subscription,
    Task,
    TestEpisode,
    TaskStatus,
    User,
    Workstream,
    Workspace,
    WorkspaceMembership,
)

log = logging.getLogger(__name__)

M = TypeVar(
    "M",
    AgentConversation,
    User,
    Workspace,
    WorkspaceMembership,
    Machine,
    Project,
    ProjectWorkstream,
    Workstream,
    Task,
    Question,
    Runner,
    Resource,
    Feedback,
    Finding,
    Subscription,
    HumanTask,
    IssueRun,
    Story,
    TestEpisode,
    OrchestratorRun,
    Directive,
    Checkout,
)

_COLLECTIONS: dict[type, str] = {
    AgentConversation: "agent_conversations",
    User: "users",
    Workspace: "workspaces",
    WorkspaceMembership: "workspace_memberships",
    Machine: "machines",
    Project: "projects",
    ProjectWorkstream: "project_workstreams",
    Workstream: "workstreams",
    Task: "tasks",
    Question: "questions",
    Runner: "runners",
    Resource: "resources",
    Feedback: "feedback",
    Finding: "findings",
    Subscription: "subscriptions",
    HumanTask: "human_tasks",
    IssueRun: "issue_runs",
    Story: "stories",
    TestEpisode: "test_episodes",
    OrchestratorRun: "orchestrator_runs",
    Directive: "directives",
    Checkout: "checkouts",
}


def _created_at(obj) -> float:
    return getattr(obj, "created_at", 0.0)


def _matches(raw: dict, key: str, value) -> bool:
    if key == "workspace_id" and value == DEFAULT_WORKSPACE_ID and key not in raw:
        return True
    return raw.get(key) == value


class StoreBase(ABC):
    """The persistence contract every caller depends on. Subclasses implement
    the primitives; the convenience queries are derived once here."""

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
    def get_org_context(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> str: ...

    @abstractmethod
    def set_org_context(self, text: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None: ...

    @abstractmethod
    def claim_leader(
        self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> str: ...

    @abstractmethod
    def release_leader(self, holder: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
        """Release the leader lease iff `holder` still owns it."""

    # -- derived queries (depend only on list) -------------------------------

    def open_questions(self, project_id: str) -> list[Question]:
        return self.list(Question, project_id=project_id, status=QuestionStatus.open)

    def tasks_in(self, project_id: str, status: TaskStatus) -> list[Task]:
        return self.list(Task, project_id=project_id, status=status)


class MemoryStore(StoreBase):
    """In-process store for tests and dev. An RLock makes every operation
    thread-safe, so `update` is genuinely atomic across the request threadpool
    and the supervisor loop."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {name: {} for name in _COLLECTIONS.values()}
        self.org_context: str = ""
        self._org_contexts: dict[str, str] = {}
        self._lease: dict | None = None
        self._leases: dict[str, dict] = {}
        self._lock = threading.RLock()

    def put(self, obj: M) -> M:
        with self._lock:
            self._data[_COLLECTIONS[type(obj)]][obj.id] = obj.model_dump()
        return obj

    def get(self, model: type[M], id: str) -> M | None:
        with self._lock:
            raw = self._data[_COLLECTIONS[model]].get(id)
        return model.model_validate(raw) if raw else None

    def list(self, model: type[M], *, limit: int | None = None, **filters) -> list[M]:
        with self._lock:
            rows = list(self._data[_COLLECTIONS[model]].values())
        out = [
            model.model_validate(raw)
            for raw in rows
            if all(_matches(raw, k, v) for k, v in filters.items())
        ]
        out.sort(key=_created_at)
        return out[-limit:] if limit is not None else out

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        with self._lock:
            collection = self._data[_COLLECTIONS[model]]
            raw = collection.get(id)
            if raw is None:
                return None
            obj = model.model_validate(raw)
            mutate(obj)
            collection[obj.id] = obj.model_dump()
            return obj

    def delete(self, model: type[M], id: str) -> None:
        with self._lock:
            self._data[_COLLECTIONS[model]].pop(id, None)

    def raw_docs(self, collection: str) -> list[dict]:
        with self._lock:
            return [dict(raw) for raw in self._data[collection].values()]

    def get_org_context(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> str:
        with self._lock:
            if workspace_id == DEFAULT_WORKSPACE_ID:
                return self.org_context
            return self._org_contexts.get(workspace_id, "")

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        with self._lock:
            if workspace_id == DEFAULT_WORKSPACE_ID:
                self.org_context = text
            else:
                self._org_contexts[workspace_id] = text

    def claim_leader(
        self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> str:
        """Claim or renew the single-chief lease. Returns the holder
        that owns the lease after this attempt; callers losing the claim see
        the competing holder's name. A lease is free once its TTL lapses, so
        a crashed chief is superseded within ttl_s."""
        with self._lock:
            lease = self._lease if workspace_id == DEFAULT_WORKSPACE_ID else self._leases.get(workspace_id)
            if lease and lease["holder"] != holder and lease["expires"] > time.time():
                return lease["holder"]
            lease = {"holder": holder, "expires": time.time() + ttl_s}
            if workspace_id == DEFAULT_WORKSPACE_ID:
                self._lease = lease
            else:
                self._leases[workspace_id] = lease
            return holder

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
        with self._lock:
            if workspace_id == DEFAULT_WORKSPACE_ID:
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
    """JSON-on-disk store for local chief runs. Same in-process locking
    as MemoryStore, but every mutation is flushed to ``root/<collection>/<id>.json``."""

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
        for collection in _COLLECTIONS.values():
            directory = self._collection_dir(collection)
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                raw = _read_json_file(path, strict=False)
                if raw is None:
                    continue
                self._data[collection][raw["id"]] = raw

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
        if workspace_id == DEFAULT_WORKSPACE_ID:
            _atomic_write_json(self._settings_path("org_context.json"), {"text": text})
        else:
            _atomic_write_json(
                self._settings_path(f"org_context_{workspace_id}.json"),
                {"text": text},
            )

    def _persist_lease(self, workspace_id: str, lease: dict) -> None:
        if workspace_id == DEFAULT_WORKSPACE_ID:
            _atomic_write_json(self._settings_path("leader_lease.json"), lease)
        else:
            _atomic_write_json(self._settings_path(f"leader_lease_{workspace_id}.json"), lease)

    def _delete_lease(self, workspace_id: str) -> None:
        if workspace_id == DEFAULT_WORKSPACE_ID:
            self._settings_path("leader_lease.json").unlink(missing_ok=True)
        else:
            self._settings_path(f"leader_lease_{workspace_id}.json").unlink(missing_ok=True)

    def put(self, obj: M) -> M:
        with self._lock:
            collection = _COLLECTIONS[type(obj)]
            self._data[collection][obj.id] = obj.model_dump()
            self._persist_doc(collection, obj.id, self._data[collection][obj.id])
        return obj

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        with self._lock:
            collection = self._data[_COLLECTIONS[model]]
            raw = collection.get(id)
            if raw is None:
                return None
            obj = model.model_validate(raw)
            mutate(obj)
            collection[obj.id] = obj.model_dump()
            self._persist_doc(_COLLECTIONS[model], id, collection[id])
            return obj

    def delete(self, model: type[M], id: str) -> None:
        with self._lock:
            collection = _COLLECTIONS[model]
            self._data[collection].pop(id, None)
            self._delete_doc(collection, id)

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        with self._lock:
            if workspace_id == DEFAULT_WORKSPACE_ID:
                self.org_context = text
            else:
                self._org_contexts[workspace_id] = text
            self._persist_org_context(workspace_id, text)

    def claim_leader(
        self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> str:
        with self._lock:
            lease = self._lease if workspace_id == DEFAULT_WORKSPACE_ID else self._leases.get(workspace_id)
            if lease and lease["holder"] != holder and lease["expires"] > time.time():
                return lease["holder"]
            lease = {"holder": holder, "expires": time.time() + ttl_s}
            if workspace_id == DEFAULT_WORKSPACE_ID:
                self._lease = lease
            else:
                self._leases[workspace_id] = lease
            self._persist_lease(workspace_id, lease)
            return holder

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
        with self._lock:
            if workspace_id == DEFAULT_WORKSPACE_ID:
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
        self._db.collection(_COLLECTIONS[type(obj)]).document(obj.id).set(obj.model_dump())
        return obj

    def get(self, model: type[M], id: str) -> M | None:
        snap = self._db.collection(_COLLECTIONS[model]).document(id).get()
        return model.model_validate(snap.to_dict()) if snap.exists else None

    def list(self, model: type[M], *, limit: int | None = None, **filters) -> list[M]:
        from google.cloud.firestore_v1 import FieldFilter

        query = self._db.collection(_COLLECTIONS[model])
        post_filters = {}
        for k, v in filters.items():
            if k == "workspace_id" and v == DEFAULT_WORKSPACE_ID:
                post_filters[k] = v
                continue
            query = query.where(filter=FieldFilter(k, "==", v))
            post_filters[k] = v
        out = []
        for snap in query.stream():
            raw = snap.to_dict()
            if all(_matches(raw, k, v) for k, v in post_filters.items()):
                out.append(model.model_validate(raw))
        out.sort(key=_created_at)
        return out[-limit:] if limit is not None else out

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        from google.cloud import firestore

        ref = self._db.collection(_COLLECTIONS[model]).document(id)
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
        self._db.collection(_COLLECTIONS[model]).document(id).delete()

    def raw_docs(self, collection: str) -> list[dict]:
        return [snap.to_dict() for snap in self._db.collection(collection).stream()]

    def get_org_context(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> str:
        snap = (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("settings")
            .document("org_context")
            .get()
        )
        if not snap.exists and workspace_id == DEFAULT_WORKSPACE_ID:
            snap = self._db.collection("settings").document("org_context").get()
        return snap.to_dict().get("text", "") if snap.exists else ""

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("settings")
            .document("org_context")
            .set({"text": text})
        )

    def claim_leader(
        self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> str:
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

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
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
    """Write-through in-memory cache over a backing store, for the chief.

    The leader lease guarantees exactly one chief per workspace, and every
    other writer (runners, web UI, CLI) mutates state through that chief's
    API — so the chief can serve all reads from process memory and only pay
    the backing store for writes. Without this, the supervisor tick and the
    runner poll/heartbeat loops re-scan Firestore around the clock (~1.5M
    document reads per idle day, measured).

    Collections hydrate once at construction. Mutations persist to the
    backing store *first*, then commit to memory, so a backend failure
    surfaces as an exception while memory still mirrors durable state.
    Leases and org context pass through uncached: the lease is the
    cross-process fencing primitive and must stay authoritative in the
    backing store.
    """

    def __init__(self, inner: StoreBase) -> None:
        super().__init__()
        self.inner = inner
        for collection in _COLLECTIONS.values():
            for raw in inner.raw_docs(collection):
                self._data[collection][raw["id"]] = raw

    def put(self, obj: M) -> M:
        with self._lock:
            self.inner.put(obj)
            return super().put(obj)

    def update(self, model: type[M], id: str, mutate: Callable[[M], None]) -> M | None:
        # The in-process lock is the atomicity guarantee (single-writer chief);
        # the backing store's own concurrent-writer protection is not needed.
        with self._lock:
            collection = self._data[_COLLECTIONS[model]]
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
            self.inner.delete(model, id)
            super().delete(model, id)

    def get_org_context(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> str:
        return self.inner.get_org_context(workspace_id)

    def set_org_context(self, text: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.inner.set_org_context(text, workspace_id)

    def claim_leader(
        self, holder: str, ttl_s: float, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> str:
        return self.inner.claim_leader(holder, ttl_s, workspace_id)

    def release_leader(self, holder: str, workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
        return self.inner.release_leader(holder, workspace_id)
