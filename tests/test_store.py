"""MemoryStore contract: filtered/limited listing, atomic update (incl. under
concurrency), and the derived convenience queries. FirestoreStore mirrors this
API but needs GCP, so it is exercised only in deployment, not here."""

import json
import threading
import time

import pytest

from hive.models import Question, QuestionStatus, Project, Resource, Task, TaskStatus, User
from hive.persistence.store import FileStore, MemoryStore
from hive.config.storage import copy_store


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


def test_filtered_queries():
    store = MemoryStore()
    q_open = store.put(Question(project_id="p", text="open?"))
    store.put(Question(project_id="p", text="answered?", status=QuestionStatus.answered))
    store.put(Task(project_id="p", workstream_id="w", repo="r", instructions="i",
                   status=TaskStatus.running))

    open_qs = store.list(Question, project_id="p", status=QuestionStatus.open)
    assert [q.id for q in open_qs] == [q_open.id]
    assert len(store.list(Task, project_id="p", status=TaskStatus.running)) == 1
    assert store.list(Task, project_id="p", status=TaskStatus.done) == []


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


def test_file_store_persists_across_restart(tmp_path):
    store1 = FileStore(tmp_path)
    project = store1.put(Project(name="durable"))
    store1.set_org_context("org notes")

    store2 = FileStore(tmp_path)
    loaded = store2.get(Project, project.id)
    assert loaded is not None and loaded.name == "durable"
    assert store2.get_org_context() == "org notes"


def test_file_store_skips_corrupt_docs(tmp_path):
    users = tmp_path / "users"
    users.mkdir(parents=True)
    good = User(id="github:good", github_login="good")
    (users / "github:good.json").write_text(json.dumps(good.model_dump()))
    (users / "github:bad.json").write_text('{"id":"github:bad","broken":')

    store = FileStore(tmp_path)

    assert store.get(User, "github:good") is not None
    assert store.get(User, "github:bad") is None


def test_file_store_rejects_corrupt_settings(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir(parents=True)
    (settings / "org_context.json").write_text("{bad")

    with pytest.raises(ValueError, match="Corrupt store file"):
        FileStore(tmp_path)


def test_file_store_concurrent_puts_same_doc(tmp_path):
    """Parallel auth touches must not race on a shared .tmp path."""
    store = FileStore(tmp_path)
    errors: list[Exception] = []

    def touch():
        try:
            store.put(
                User(
                    id="github:test",
                    github_login="test",
                    display_name="test",
                    last_seen=time.time(),
                )
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=touch) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.get(User, "github:test") is not None


def test_org_context_multi_workspace():
    store = MemoryStore()
    store.set_org_context("default notes")
    store.set_org_context("team notes", workspace_id="team-a")
    store.set_org_context("other notes", workspace_id="team-b")

    assert store.get_org_context() == "default notes"
    assert store.get_org_context(workspace_id="team-a") == "team notes"
    assert store.get_org_context(workspace_id="team-b") == "other notes"
    assert store.get_org_context(workspace_id="unknown") == ""


def test_release_leader():
    store = MemoryStore()
    assert store.claim_leader("a", 60) == "a"
    assert store.release_leader("b") is False   # b doesn't hold the lease
    assert store.release_leader("a") is True    # a releases successfully
    assert store.release_leader("a") is False   # nothing left to release
    assert store.claim_leader("b", 60) == "b"   # lease is now free


def test_release_leader_per_workspace():
    store = MemoryStore()
    store.claim_leader("a", 60)
    store.claim_leader("x", 60, workspace_id="ws-1")

    assert store.release_leader("x", workspace_id="ws-1") is True
    assert store.release_leader("x", workspace_id="ws-1") is False  # already released
    assert store.claim_leader("y", 60, workspace_id="ws-1") == "y"  # free now
    assert store.claim_leader("z", 60) == "a"   # default workspace unaffected


def test_file_store_persists_workspace_org_context(tmp_path):
    store1 = FileStore(tmp_path)
    store1.set_org_context("default context")
    store1.set_org_context("team context", workspace_id="team-x")

    store2 = FileStore(tmp_path)
    assert store2.get_org_context() == "default context"
    assert store2.get_org_context(workspace_id="team-x") == "team context"
    assert store2.get_org_context(workspace_id="unknown") == ""


def test_copy_store_between_backends(tmp_path):
    source = FileStore(tmp_path)
    source.put(Project(name="migrate-me"))
    source.set_org_context("keep this")

    dest = MemoryStore()
    counts = copy_store(source, dest)

    assert counts["projects"] == 1
    assert dest.list(Project)[0].name == "migrate-me"
    assert dest.get_org_context() == "keep this"
