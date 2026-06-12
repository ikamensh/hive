"""Persistence. FirestoreStore for production, MemoryStore for tests.

Both expose the same duck-typed API: typed CRUD per collection plus a few
filtered queries. Documents are pydantic models serialized to dicts.
"""

from __future__ import annotations

from typing import TypeVar

from hive.models import (
    Feedback,
    HumanTask,
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
    "M", Project, Workstream, Task, Question, Runner, Resource, Feedback, Subscription, HumanTask
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
}


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {name: {} for name in _COLLECTIONS.values()}
        self.org_context: str = ""

    def put(self, obj: M) -> M:
        self._data[_COLLECTIONS[type(obj)]][obj.id] = obj.model_dump()
        return obj

    def get(self, model: type[M], id: str) -> M | None:
        raw = self._data[_COLLECTIONS[model]].get(id)
        return model.model_validate(raw) if raw else None

    def list(self, model: type[M], **filters) -> list[M]:
        out = []
        for raw in self._data[_COLLECTIONS[model]].values():
            if all(raw.get(k) == v for k, v in filters.items()):
                out.append(model.model_validate(raw))
        return sorted(out, key=lambda o: o.created_at if hasattr(o, "created_at") else 0)

    def delete(self, model: type[M], id: str) -> None:
        self._data[_COLLECTIONS[model]].pop(id, None)

    def get_org_context(self) -> str:
        return self.org_context

    def set_org_context(self, text: str) -> None:
        self.org_context = text

    # -- convenience queries used by supervisor/dispatcher -------------------

    def open_questions(self, project_id: str) -> list[Question]:
        return self.list(Question, project_id=project_id, status=QuestionStatus.open)

    def tasks_in(self, project_id: str, status: TaskStatus) -> list[Task]:
        return self.list(Task, project_id=project_id, status=status)


class FirestoreStore(MemoryStore):
    """Same API, backed by Firestore. Inherits the convenience queries
    (they call self.list / self.get which are overridden here)."""

    def __init__(self, project: str, database: str = "(default)") -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project, database=database)

    def put(self, obj: M) -> M:
        self._db.collection(_COLLECTIONS[type(obj)]).document(obj.id).set(obj.model_dump())
        return obj

    def get(self, model: type[M], id: str) -> M | None:
        snap = self._db.collection(_COLLECTIONS[model]).document(id).get()
        return model.model_validate(snap.to_dict()) if snap.exists else None

    def list(self, model: type[M], **filters) -> list[M]:
        from google.cloud.firestore_v1 import FieldFilter

        query = self._db.collection(_COLLECTIONS[model])
        for k, v in filters.items():
            query = query.where(filter=FieldFilter(k, "==", v))
        out = [model.model_validate(snap.to_dict()) for snap in query.stream()]
        return sorted(out, key=lambda o: o.created_at if hasattr(o, "created_at") else 0)

    def delete(self, model: type[M], id: str) -> None:
        self._db.collection(_COLLECTIONS[model]).document(id).delete()

    def get_org_context(self) -> str:
        snap = self._db.collection("settings").document("org_context").get()
        return snap.to_dict().get("text", "") if snap.exists else ""

    def set_org_context(self, text: str) -> None:
        self._db.collection("settings").document("org_context").set({"text": text})
