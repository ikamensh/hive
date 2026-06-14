"""Persistence. `StoreBase` is the contract; `MemoryStore` (tests/dev) and
`FirestoreStore` (prod) are independent implementations of it.

Documents are pydantic models serialized to dicts. `update` is the atomic
read-modify-write: the way to mutate a document without clobbering a concurrent
writer (the supervisor loop and the request threadpool both touch tasks). Plain
`put` is last-write-wins and fine for freshly-built objects and uncontended writes.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, TypeVar

from hive.models import (
    Feedback,
    HumanTask,
    OrchestratorRun,
    Project,
    Question,
    QuestionStatus,
    Resource,
    Runner,
    Subscription,
    Task,
    TaskStatus,
    Workstream,
)

M = TypeVar(
    "M",
    Project,
    Workstream,
    Task,
    Question,
    Runner,
    Resource,
    Feedback,
    Subscription,
    HumanTask,
    OrchestratorRun,
)

_COLLECTIONS: dict[type, str] = {
    Project: "projects",
    Workstream: "workstreams",
    Task: "tasks",
    Question: "questions",
    Runner: "runners",
    Resource: "resources",
    Feedback: "feedback",
    Subscription: "subscriptions",
    HumanTask: "human_tasks",
    OrchestratorRun: "orchestrator_runs",
}


def _created_at(obj) -> float:
    return getattr(obj, "created_at", 0.0)


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
    def get_org_context(self) -> str: ...

    @abstractmethod
    def set_org_context(self, text: str) -> None: ...

    @abstractmethod
    def claim_leader(self, holder: str, ttl_s: float) -> str: ...

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
        self._lease: dict | None = None
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
            if all(raw.get(k) == v for k, v in filters.items())
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

    def get_org_context(self) -> str:
        with self._lock:
            return self.org_context

    def set_org_context(self, text: str) -> None:
        with self._lock:
            self.org_context = text

    def claim_leader(self, holder: str, ttl_s: float) -> str:
        """Claim or renew the single-control-plane lease. Returns the holder
        that owns the lease after this attempt; callers losing the claim see
        the competing holder's name. A lease is free once its TTL lapses, so
        a crashed control plane is superseded within ttl_s."""
        with self._lock:
            lease = self._lease
            if lease and lease["holder"] != holder and lease["expires"] > time.time():
                return lease["holder"]
            self._lease = {"holder": holder, "expires": time.time() + ttl_s}
            return holder


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
        for k, v in filters.items():
            query = query.where(filter=FieldFilter(k, "==", v))
        out = [model.model_validate(snap.to_dict()) for snap in query.stream()]
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

    def get_org_context(self) -> str:
        snap = self._db.collection("settings").document("org_context").get()
        return snap.to_dict().get("text", "") if snap.exists else ""

    def set_org_context(self, text: str) -> None:
        self._db.collection("settings").document("org_context").set({"text": text})

    def claim_leader(self, holder: str, ttl_s: float) -> str:
        from google.cloud import firestore

        ref = self._db.collection("settings").document("leader_lease")
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
