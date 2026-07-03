"""CachedStore: the chief's write-through cache over the backing store.

Properties verified (chosen to survive refactors):
- hydration: documents already in the backing store are readable through the
  cache immediately after construction;
- mirror invariance: after any sequence of put/update/delete, cache and
  backing store agree document-for-document;
- read isolation: get/list never touch the backing store after hydration —
  this is the whole point (Firestore bills per document read; the idle chief
  was burning ~1.5M reads/day scanning collections it already knew);
- persist-first ordering: a failing backend write leaves memory unchanged and
  raises, so memory always mirrors durable state;
- fencing pass-through: leases stay authoritative in the backing store so a
  second chief is still fenced out even though reads are cached.
"""

import pytest

from hive.models import Project, Runner, Task, TaskStatus
from hive.persistence.store import CachedStore, MemoryStore


class CountingStore(MemoryStore):
    """MemoryStore spy: counts read-path calls reaching the backing store."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get(self, model, id):
        self.reads += 1
        return super().get(model, id)

    def list(self, model, *, limit=None, **filters):
        self.reads += 1
        return super().list(model, limit=limit, **filters)


class FailingStore(MemoryStore):
    """Backing store whose writes fail after construction-time hydration."""

    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    def put(self, obj):
        if self.fail:
            raise ConnectionError("backend down")
        return super().put(obj)

    def delete(self, model, id):
        if self.fail:
            raise ConnectionError("backend down")
        super().delete(model, id)


def test_hydration_exposes_preexisting_documents():
    inner = MemoryStore()
    project = inner.put(Project(name="p", spec_repo="s"))
    runner = inner.put(Runner(name="r"))

    cached = CachedStore(inner)

    assert cached.get(Project, project.id).name == "p"
    assert [r.id for r in cached.list(Runner)] == [runner.id]


def test_mutations_mirror_into_backing_store():
    inner = MemoryStore()
    cached = CachedStore(inner)

    project = cached.put(Project(name="p", spec_repo="s"))
    task = cached.put(
        Task(project_id=project.id, workstream_id="w", repo="r", instructions="i")
    )
    cached.update(Task, task.id, lambda t: setattr(t, "status", TaskStatus.running))
    cached.delete(Project, project.id)

    for model in (Project, Task, Runner):
        assert [o.model_dump() for o in cached.list(model)] == [
            o.model_dump() for o in inner.list(model)
        ]
    assert inner.get(Task, task.id).status == TaskStatus.running


def test_reads_never_reach_backing_store_after_hydration():
    inner = CountingStore()
    inner.put(Project(name="p", spec_repo="s"))
    cached = CachedStore(inner)
    inner.reads = 0

    project = cached.list(Project)[0]
    cached.get(Project, project.id)
    cached.list(Task, project_id=project.id, status=TaskStatus.pending)
    cached.put(Runner(name="r"))
    cached.list(Runner)

    assert inner.reads == 0


def test_update_of_missing_document_returns_none():
    cached = CachedStore(MemoryStore())
    assert cached.update(Task, "nope", lambda t: None) is None


def test_failed_backend_write_raises_and_leaves_memory_unchanged():
    inner = FailingStore()
    cached = CachedStore(inner)
    project = cached.put(Project(name="p", spec_repo="s"))

    inner.fail = True
    with pytest.raises(ConnectionError):
        cached.put(Project(name="q", spec_repo="s2"))
    with pytest.raises(ConnectionError):
        cached.update(Project, project.id, lambda p: setattr(p, "name", "renamed"))
    with pytest.raises(ConnectionError):
        cached.delete(Project, project.id)

    assert [p.name for p in cached.list(Project)] == ["p"]
    assert cached.get(Project, project.id).name == "p"


def test_leases_pass_through_so_second_chief_is_fenced():
    inner = MemoryStore()
    cached = CachedStore(inner)

    assert cached.claim_leader("chief-a", ttl_s=60) == "chief-a"
    # A competing chief talking to the same backing store loses the claim.
    assert inner.claim_leader("chief-b", ttl_s=60) == "chief-a"
    assert cached.release_leader("chief-a") is True
    assert inner.claim_leader("chief-b", ttl_s=60) == "chief-b"


def test_org_context_passes_through():
    inner = MemoryStore()
    cached = CachedStore(inner)
    cached.set_org_context("rules of the org")
    assert inner.get_org_context() == "rules of the org"
    assert cached.get_org_context() == "rules of the org"
