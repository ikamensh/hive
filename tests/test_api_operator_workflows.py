"""Operator journeys through HTTP, including correction after rejected input."""

import pytest

from hive.models import Question
from hive.persistence import MemoryStore
from tests.test_inbox_api import client_for
from tests.test_users_roles import login, make_app


@pytest.fixture
def client():
    return client_for(MemoryStore())


def test_project_setup_rejects_blank_names_and_negative_budgets_before_archiving(client):
    """A project needs a visible name and a valid budget; rejected edits preserve it."""
    invalid = client.post("/api/projects", json={"name": " \t\n "})
    assert invalid.status_code == 400
    assert client.get("/api/projects").json() == []

    created = client.post("/api/projects", json={"name": "  Catalog migration  "})
    assert created.status_code == 200
    project = created.json()
    assert project["name"] == "Catalog migration"
    path = f"/api/projects/{project['id']}"
    rejected = client.patch(path, json={"name": "Changed", "daily_budget_usd": -5})
    assert rejected.status_code == 422
    unchanged = client.get(path).json()["project"]
    assert unchanged["name"] == project["name"]
    assert unchanged["daily_budget_usd"] == project["daily_budget_usd"]

    accepted = client.patch(path, json={"daily_budget_usd": 0, "archived": True})
    assert accepted.status_code == 200
    assert accepted.json()["daily_budget_usd"] == 0
    assert client.get("/api/projects").json() == []
    assert [row["id"] for row in client.get("/api/projects?include_archived=true").json()] == [project["id"]]
    assert client.patch(path, json={"archived": False}).status_code == 200
    assert [row["id"] for row in client.get("/api/projects").json()] == [project["id"]]


def test_blank_answer_keeps_question_in_inbox_until_operator_answers(client):
    """Whitespace is not a decision and must not clear the outstanding question."""
    project = client.post("/api/projects", json={"name": "Search semantics"}).json()
    path = f"/api/projects/{project['id']}"
    client.patch(path, json={"spec_repo": "https://example.com/search.git"}).raise_for_status()
    question = client.app.state.store.put(
        Question(project_id=project["id"], text="Should matching ignore case?")
    )
    answer_path = f"/api/questions/{question.id}/answer"
    rejected = client.post(answer_path, json={"answer": " \n "})
    assert rejected.status_code == 400
    outstanding = client.get("/api/inbox?kind=question").json()
    assert outstanding["total"] == 1
    assert outstanding["items"][0]["question"]["status"] == "open"
    assert outstanding["items"][0]["question"]["answer"] == ""

    answer = "  Ignore case,\nbut preserve accents.  "
    accepted = client.post(answer_path, json={"answer": answer})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "answered"
    assert accepted.json()["answer"] == answer
    assert client.get("/api/inbox?kind=question").json()["total"] == 0
    history = client.get(path).json()["questions"]
    assert history[0]["answer"] == answer


def test_manual_todo_corrects_invalid_input_then_completes_from_inbox(client):
    """Malformed or nameless todos must not create dead-end entries in Inbox."""
    for body, status in (
        ({}, 422),
        ({"title": "  "}, 400),
        ({"title": "Restore access", "assignee_user_id": None}, 422),
        ({"title": "Restore access", "assignee_user_id": "missing-member"}, 400),
    ):
        rejected = client.post("/api/human-todos", json=body)
        assert rejected.status_code == status
        assert client.get("/api/human-todos").json() == []

    project = client.post("/api/projects", json={"name": "Repository move"}).json()
    instructions = "Grant the runner access.\n\nThen retry the repository probe."
    created = client.post("/api/human-todos", json={
        "title": "  Restore repository access  ", "instructions": instructions,
        "project_id": project["id"],
    })
    assert created.status_code == 200
    todo = created.json()
    assert todo["title"] == "Restore repository access"
    assert todo["instructions"] == instructions
    row, = client.get("/api/inbox?kind=todo").json()["items"]
    assert row["project_name"] == "Repository move"
    assert row["todo"]["id"] == todo["id"]
    assert client.post(f"/api/human-todos/{todo['id']}/done").json()["status"] == "done"
    assert client.get("/api/inbox?kind=todo").json()["total"] == 0
    assert client.get("/api/human-todos").json()[0]["status"] == "done"


def test_subscription_registration_validates_license_and_owner_then_respects_control():
    """Only a supported license assigned to a real member should enter capacity."""
    client, auth, _ = make_app(MemoryStore())
    alice_headers, alice = login(auth, "alice")
    bob_headers, bob = login(auth, "bob")
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"},
                 headers=alice_headers).raise_for_status()
    for body, status in (
        ({}, 422),
        ({"provider": "codex", "licensing_mode": "shared"}, 422),
        ({"provider": " "}, 400),
        ({"provider": "unsupported-agent"}, 400),
        ({"provider": "codex", "owner_user_id": "missing-member"}, 400),
    ):
        rejected = client.post("/api/subscriptions", json=body, headers=alice_headers)
        assert rejected.status_code == status
        assert client.get("/api/subscriptions", headers=alice_headers).json() == []

    rejected = client.post("/api/subscriptions", json={
        "provider": "codex", "owner_user_id": alice.id,
    }, headers=bob_headers)
    assert rejected.status_code == 403
    assert client.get("/api/subscriptions", headers=alice_headers).json() == []

    created = client.post("/api/subscriptions", json={
        "provider": " codex ", "plan": "Team account",
        "licensing_mode": "machine_bound", "owner_user_id": bob.id,
    }, headers=alice_headers)
    assert created.status_code == 200
    license = created.json()
    assert license["provider"] == "codex"
    assert license["owner_user_id"] == bob.id
    assert license["licensing_mode"] == "machine_bound"
    assert client.get("/api/subscriptions", headers=bob_headers).json() == [license]
    assert client.delete(f"/api/subscriptions/{license['id']}", headers=bob_headers).status_code == 200
    assert client.get("/api/subscriptions", headers=alice_headers).json() == []


def test_rejected_machine_owner_change_preserves_power_settings_and_todo_routing():
    """A rejected settings save must neither change power behavior nor move an action."""
    client, auth, _ = make_app(MemoryStore())
    alice_headers, alice = login(auth, "alice")
    bob_headers, bob = login(auth, "bob")
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"},
                 headers=alice_headers).raise_for_status()
    client.post("/api/runners/register", json={
        "name": "Build server", "backends": ["codex"], "owner_user_id": bob.id,
    }, headers={"X-Hive-Token": "t"}).raise_for_status()
    machine, = client.get("/api/resources", headers=alice_headers).json()["machines"]
    path = f"/api/machines/{machine['id']}"
    todo = client.post("/api/human-todos", json={
        "title": "Fix codex login on Build server", "assignee_user_id": bob.id,
    }, headers=alice_headers).json()

    for headers, owner, status in (
        (alice_headers, "missing-member", 400),
        (bob_headers, alice.id, 403),
    ):
        rejected = client.patch(path, json={
            "power_policy": "on_demand", "idle_stop_minutes": 10,
            "owner_user_id": owner,
        }, headers=headers)
        assert rejected.status_code == status
        current, = client.get("/api/resources", headers=alice_headers).json()["machines"]
        assert current == machine
        row, = client.get("/api/inbox?kind=todo&scope=yours", headers=bob_headers).json()["items"]
        assert row["todo"]["id"] == todo["id"]

    accepted = client.patch(path, json={
        "power_policy": "on_demand", "idle_stop_minutes": 10, "owner_user_id": alice.id,
    }, headers=alice_headers)
    assert accepted.status_code == 200
    assert accepted.json()["owner_user_id"] == alice.id
    assert accepted.json()["power_policy"] == "on_demand"
    assert accepted.json()["idle_stop_minutes"] == 10
    assert client.get("/api/inbox?scope=yours", headers=bob_headers).json()["total"] == 0
    row, = client.get("/api/inbox?kind=todo", headers=alice_headers).json()["items"]
    assert row["todo"]["assignee_user_id"] == alice.id
    client.patch(path, json={"idle_stop_minutes": 25}, headers=alice_headers).raise_for_status()
    current, = client.get("/api/resources", headers=alice_headers).json()["machines"]
    assert current["idle_stop_minutes"] == 25


@pytest.mark.parametrize("budget", ["1e999", "-1e999"])
def test_overflowing_budget_returns_actionable_validation_error_without_saving(client, budget):
    """Valid JSON can overflow a float; its error must still be valid HTTP JSON."""
    project = client.post("/api/projects", json={"name": "Catalog migration"}).json()
    path = f"/api/projects/{project['id']}"
    rejected = client.patch(path,
        content='{"name": "Changed", "daily_budget_usd": ' + budget + '}',
        headers={"content-type": "application/json"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["loc"] == ["body", "daily_budget_usd"]
    assert "finite" in rejected.json()["detail"][0]["msg"]
    unchanged = client.get(path).json()["project"]
    assert unchanged["name"] == project["name"]
    assert unchanged["daily_budget_usd"] == project["daily_budget_usd"]
