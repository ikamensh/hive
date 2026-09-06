"""Operator journeys through HTTP, including correction after rejected input."""

import pytest

from hive.models import Question
from hive.persistence import MemoryStore
from tests.test_inbox_api import client_for


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
