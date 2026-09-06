"""Operator journeys through HTTP, including correction after rejected input."""

import pytest

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
