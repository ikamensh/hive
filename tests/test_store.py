"""MemoryStore contract: filtered/limited listing, atomic update (incl. under
concurrency), and the derived convenience queries. FirestoreStore mirrors this
API but needs GCP, so it is exercised only in deployment, not here."""

import threading
import time

from hive.models import Question, QuestionStatus, Resource, Task, TaskStatus
from hive.store import MemoryStore, StoreBase


def test_memory_store_is_a_storebase():
    assert isinstance(MemoryStore(), StoreBase)


def test_list_filters_orders_and_limits():
    store = MemoryStore()
    made = []
    for i in range(5):
        made.append(store.put(Task(project_id="p", workstream_id="w", repo="r", instructions=str(i))))
        time.sleep(0.001)  # distinct created_at so ordering is deterministic
    store.put(Task(project_id="other", workstream_id="w", repo="r", instructions="x"))

    listed = store.list(Task, project_id="p")
    assert [t.instructions for t in listed] == ["0", "1", "2", "3", "4"]  # oldest→newest

    recent = store.list(Task, project_id="p", limit=2)
    assert [t.instructions for t in recent] == ["3", "4"]  # most recent, still in order


def test_update_mutates_atomically_and_returns_object():
    store = MemoryStore()
    task = store.put(Task(project_id="p", workstream_id="w", repo="r", instructions="i"))

    def finish(t):
        t.status = TaskStatus.done
        t.cost_usd = 1.5

    updated = store.update(Task, task.id, finish)
    assert updated.status == TaskStatus.done and updated.cost_usd == 1.5
    assert store.get(Task, task.id).status == TaskStatus.done


def test_update_missing_returns_none():
    assert MemoryStore().update(Task, "nope", lambda t: None) is None


def test_update_is_lossless_under_concurrent_writers():
    """Without atomic read-modify-write, parallel increments lose updates."""
    store = MemoryStore()
    resource = store.put(Resource(runner_id="r", backend="cursor"))
    workers = 20
    per_worker = 50

    def bump():
        for _ in range(per_worker):
            store.update(Resource, resource.id, lambda r: setattr(r, "total_tasks", r.total_tasks + 1))

    threads = [threading.Thread(target=bump) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store.get(Resource, resource.id).total_tasks == workers * per_worker


def test_convenience_queries():
    store = MemoryStore()
    q_open = store.put(Question(project_id="p", text="open?"))
    store.put(Question(project_id="p", text="answered?", status=QuestionStatus.answered))
    store.put(Task(project_id="p", workstream_id="w", repo="r", instructions="i",
                   status=TaskStatus.running))

    assert [q.id for q in store.open_questions("p")] == [q_open.id]
    assert len(store.tasks_in("p", TaskStatus.running)) == 1
    assert store.tasks_in("p", TaskStatus.done) == []


def test_leader_lease():
    store = MemoryStore()
    assert store.claim_leader("a", 60) == "a"
    assert store.claim_leader("a", 60) == "a"  # renew by the holder
    assert store.claim_leader("b", 60) == "a"  # contender sees the live holder
    assert store.claim_leader("b", 60, workspace_id="team-b") == "b"
    assert store.claim_leader("c", 60, workspace_id="team-b") == "b"

    expired = MemoryStore()
    assert expired.claim_leader("a", 0.0) == "a"  # holds, but lease lapses immediately
    time.sleep(0.01)
    assert expired.claim_leader("b", 60) == "b"  # a's lease expired; b takes over
