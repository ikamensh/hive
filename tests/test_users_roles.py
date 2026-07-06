"""Multi-user workspace: roles, per-user machines/licenses, and todo routing.

Everyone on the allow-list is an admin by default; a resource provider is a
read-only member who lends machines/licenses — they manage only what they own,
and auth todos route to the owner of the machine that needs hands.
"""

import time

from fastapi.testclient import TestClient

from hive.config.settings import Config
from hive.models import (
    HumanTask,
    HumanTaskStatus,
    Machine,
    Resource,
    Runner,
    Task,
    TaskStatus,
)
from hive.persistence.store import MemoryStore
from hive._control.supervisor import Supervisor

RUNNER_HEADERS = {"X-Hive-Token": "t"}


def make_app(store: MemoryStore):
    from hive.api import create_app

    config = Config(
        gcp_project="",
        gcs_bucket="",
        gh_token="",
        gemini_api_key="",
        orch_model="",
        runner_token="t",
        data_dir=None,
        auth_mode="github",
        allowed_github_users="alice,bob",
        github_client_id="client-id",
        github_client_secret="client-secret",
        auth_secret="auth-secret",
        public_url="http://testserver",
        machine_name="chief-test",
    )
    supervisor = Supervisor(
        store, lambda p, e: None, workspace_id=config.workspace_id, machine_name=config.machine_name
    )
    app = create_app(store, supervisor, config)
    return TestClient(app), app.state.auth, supervisor


def login(auth, github_login: str):
    """Session headers for a member, as if they completed the OAuth flow."""
    user, _membership = auth._ensure_user(github_login)
    return {"Authorization": f"Bearer {auth.session_token(user)}"}, user


def test_members_are_admin_by_default_and_listed():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, alice = login(auth, "alice")
    _bob_h, bob = login(auth, "bob")

    me = client.get("/api/auth/me", headers=alice_h).json()
    assert me["role"] == "admin"

    users = client.get("/api/users", headers=alice_h).json()
    assert [u["user"]["github_login"] for u in users] == ["alice", "bob"]
    assert all(u["role"] == "admin" for u in users)
    assert [u["is_you"] for u in users] == [True, False]
    assert {u["user"]["id"] for u in users} == {alice.id, bob.id}


def test_resource_provider_is_read_only_on_projects():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, _alice = login(auth, "alice")
    bob_h, bob = login(auth, "bob")

    assert client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_h).status_code == 200
    assert client.get("/api/auth/me", headers=bob_h).json()["role"] == "resource_provider"

    # Reads stay open; project edits are refused.
    created = client.post("/api/projects", json={"name": "mine"}, headers=alice_h)
    assert created.status_code == 200
    pid = created.json()["id"]
    assert client.get("/api/projects", headers=bob_h).status_code == 200
    assert client.get(f"/api/projects/{pid}", headers=bob_h).status_code == 200
    assert client.post("/api/projects", json={"name": "nope"}, headers=bob_h).status_code == 403
    assert client.patch(f"/api/projects/{pid}", json={"paused": True}, headers=bob_h).status_code == 403
    assert client.put("/api/org-context", json={"text": "x"}, headers=bob_h).status_code == 403
    # Role management is admin-only too.
    assert client.patch(f"/api/users/{bob.id}", json={"role": "admin"}, headers=bob_h).status_code == 403


def test_workspace_keeps_at_least_one_admin():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, alice = login(auth, "alice")
    _bob_h, bob = login(auth, "bob")

    assert client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_h).status_code == 200
    demote_self = client.patch(f"/api/users/{alice.id}", json={"role": "resource_provider"}, headers=alice_h)
    assert demote_self.status_code == 400
    assert "admin" in demote_self.json()["detail"]


def test_machine_claim_scopes_to_own_machines_for_providers():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, alice = login(auth, "alice")
    bob_h, bob = login(auth, "bob")
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_h)
    m1 = store.put(Machine(name="bobs-laptop"))
    m2 = store.put(Machine(name="alices-vm", owner_user_id=alice.id))

    # A provider claims an unowned machine and can release it again.
    assert client.patch(f"/api/machines/{m1.id}", json={"owner_user_id": bob.id}, headers=bob_h).status_code == 200
    assert store.get(Machine, m1.id).owner_user_id == bob.id
    # ...but cannot touch someone else's, or hand theirs to a third party.
    assert client.patch(f"/api/machines/{m2.id}", json={"owner_user_id": bob.id}, headers=bob_h).status_code == 403
    assert client.patch(f"/api/machines/{m1.id}", json={"owner_user_id": alice.id}, headers=bob_h).status_code == 403
    assert client.delete(f"/api/machines/{m2.id}", headers=bob_h).status_code == 403
    # An admin assigns freely; unknown owners are rejected.
    assert client.patch(f"/api/machines/{m2.id}", json={"owner_user_id": bob.id}, headers=alice_h).status_code == 200
    assert client.patch(f"/api/machines/{m1.id}", json={"owner_user_id": "ghost"}, headers=alice_h).status_code == 400

    users = {u["user"]["id"]: u for u in client.get("/api/users", headers=alice_h).json()}
    assert [m["name"] for m in users[bob.id]["machines"]] == ["bobs-laptop", "alices-vm"]


def test_subscriptions_are_owned_per_user():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, alice = login(auth, "alice")
    bob_h, bob = login(auth, "bob")
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_h)

    bobs = client.post("/api/subscriptions", json={"provider": "claude"}, headers=bob_h).json()
    alices = client.post("/api/subscriptions", json={"provider": "codex"}, headers=alice_h).json()
    assert bobs["owner_user_id"] == bob.id
    assert alices["owner_user_id"] == alice.id

    assert client.delete(f"/api/subscriptions/{alices['id']}", headers=bob_h).status_code == 403
    assert client.delete(f"/api/subscriptions/{bobs['id']}", headers=bob_h).status_code == 200
    # Admin can clean up anyone's license.
    again = client.post("/api/subscriptions", json={"provider": "claude"}, headers=bob_h).json()
    assert client.delete(f"/api/subscriptions/{again['id']}", headers=alice_h).status_code == 200


def test_provider_controls_resources_only_on_own_machines():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, alice = login(auth, "alice")
    bob_h, bob = login(auth, "bob")
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_h)
    bob_machine = store.put(Machine(name="bobs-laptop", owner_user_id=bob.id))
    alice_machine = store.put(Machine(name="alices-vm", owner_user_id=alice.id))
    runner_b = store.put(Runner(name="bobs-laptop", machine_id=bob_machine.id, backends=["codex"]))
    runner_a = store.put(Runner(name="alices-vm", machine_id=alice_machine.id, backends=["codex"]))
    res_b = store.put(Resource(runner_id=runner_b.id, machine_id=bob_machine.id, backend="codex"))
    res_a = store.put(Resource(runner_id=runner_a.id, machine_id=alice_machine.id, backend="codex"))

    assert client.patch(f"/api/resources/{res_b.id}", json={"enabled": False}, headers=bob_h).status_code == 200
    assert client.patch(f"/api/resources/{res_a.id}", json={"enabled": False}, headers=bob_h).status_code == 403
    # Ownership is checked before runner liveness, so the refusal is stable.
    assert client.post(f"/api/resources/{res_a.id}/probe", headers=bob_h).status_code == 403


def test_login_todo_is_assigned_to_the_machine_owner():
    """An auth block on bob's machine must land in bob's inbox: only he can
    refresh that CLI login."""
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    _alice_h, _alice = login(auth, "alice")
    _bob_h, bob = login(auth, "bob")
    machine = store.put(Machine(name="bobs-laptop", owner_user_id=bob.id))
    runner = store.put(Runner(name="bobs-laptop", machine_id=machine.id, backends=["codex"]))
    task = store.put(
        Task(
            project_id="p1",
            workstream_id="w1",
            repo="https://github.com/acme/demo",
            instructions="fix",
            backend="codex",
            status=TaskStatus.running,
            runner_id=runner.id,
            delivered=True,
        )
    )

    response = client.post(
        f"/api/tasks/{task.id}/result",
        json={"text": "codex: not authenticated", "is_error": True, "auth_blocked": True},
        headers=RUNNER_HEADERS,
    )
    assert response.status_code == 200

    todo = next(t for t in store.list(HumanTask) if t.title == "Fix codex login on bobs-laptop")
    assert todo.assignee_user_id == bob.id


def test_dark_machine_todo_is_assigned_to_the_machine_owner():
    store = MemoryStore()
    client, auth, supervisor = make_app(store)
    _bob_h, bob = login(auth, "bob")
    store.put(
        Machine(
            name="bobs-laptop",
            device_kind="server",
            owner_user_id=bob.id,
            last_seen=time.time() - 6 * 3600,
        )
    )

    supervisor.check_dark_machines()

    todo = next(t for t in store.list(HumanTask) if t.title.startswith("Bring machine"))
    assert todo.assignee_user_id == bob.id


def test_todo_completion_respects_assignee_for_providers():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, alice = login(auth, "alice")
    bob_h, bob = login(auth, "bob")
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_h)
    bobs = store.put(HumanTask(title="Fix login", instructions="", assignee_user_id=bob.id))
    alices = store.put(HumanTask(title="Rotate key", instructions="", assignee_user_id=alice.id))
    anyones = store.put(HumanTask(title="Renew domain", instructions=""))

    assert client.post(f"/api/human-todos/{bobs.id}/done", headers=bob_h).status_code == 200
    assert client.post(f"/api/human-todos/{alices.id}/done", headers=bob_h).status_code == 403
    assert client.post(f"/api/human-todos/{anyones.id}/done", headers=bob_h).status_code == 403
    # Admins clear anything.
    assert client.post(f"/api/human-todos/{anyones.id}/done", headers=alice_h).status_code == 200
    assert store.get(HumanTask, bobs.id).status == HumanTaskStatus.done


def test_claiming_a_machine_repoints_its_open_todos():
    """The login todo filed while a machine was unclaimed moves to the new
    owner's inbox on claim — no waiting for the next escalation tick."""
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, _alice = login(auth, "alice")
    _bob_h, bob = login(auth, "bob")
    machine = store.put(Machine(name="bobs-laptop"))
    store.put(Runner(name="bobs-laptop", machine_id=machine.id, backends=["codex"]))
    login_todo = store.put(HumanTask(title="Fix codex login on bobs-laptop", instructions=""))
    dark_todo = store.put(HumanTask(title="Bring machine bobs-laptop back online", instructions=""))

    client.patch(f"/api/machines/{machine.id}", json={"owner_user_id": bob.id}, headers=alice_h)

    assert store.get(HumanTask, login_todo.id).assignee_user_id == bob.id
    assert store.get(HumanTask, dark_todo.id).assignee_user_id == bob.id
