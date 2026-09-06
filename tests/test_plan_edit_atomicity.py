"""Rejected plan edits preserve accepted instructions and retained revisions."""

import pytest

from test_plans import FakeSpecRepo, _api_project, app as app

from hive._workstreams import plans
from hive._workstreams.plan_artifacts import document
from hive._workstreams.plan_document import render_plan_document
from hive.models import Plan, PlanItem, PlanRevision, Project
from hive.persistence.store import MemoryStore


def test_invalid_item_edit_preserves_current_and_historical_document(app):
    """An invalid title must fail before changing the item or publishing a
    revision, so both current and historical artifacts remain readable."""
    client, _ = app
    project = _api_project(client)
    initial = client.post(f"/api/projects/{project}/plan", json={
        "goal": "Ship", "items": [{"title": "First", "notes": "Keep this."}],
    }).raise_for_status().json()
    plan_id = initial["plan"]["id"]
    current = client.get(f"/api/plans/{plan_id}/document").raise_for_status().text
    historical = client.get(f"/api/plans/{plan_id}/document?revision=1").raise_for_status().text

    response = client.patch(f"/api/plan-items/{initial['items'][0]['id']}", json={"title": " "})

    assert response.status_code == 400
    after = client.get(f"/api/plans/{plan_id}").raise_for_status().json()
    assert after["items"] == initial["items"]
    assert after["plan"]["revision"] == initial["plan"]["revision"]
    assert after["plan"]["input_digest"] == initial["plan"]["input_digest"]
    assert client.get(f"/api/plans/{plan_id}/document").raise_for_status().text == current
    assert client.get(f"/api/plans/{plan_id}/document?revision=1").raise_for_status().text == historical


def test_added_item_key_cannot_collide_with_authored_key(app):
    """Automatically assigned keys coexist with arbitrary authored keys;
    adding work leaves both accepted revisions readable and distinct."""
    client, _ = app
    project = _api_project(client)
    initial = client.post(f"/api/projects/{project}/plan", json={
        "goal": "Ship", "items": [{"key": "item-2", "title": "First"}],
    }).raise_for_status().json()
    plan_id = initial["plan"]["id"]
    historical = client.get(f"/api/plans/{plan_id}/document?revision=1").raise_for_status().text

    response = client.post(f"/api/plans/{plan_id}/items", json={"title": "Second"})

    assert response.status_code == 200, response.text
    after = client.get(f"/api/plans/{plan_id}").raise_for_status().json()
    assert len({item["key"] for item in after["items"]}) == 2
    assert after["items"][0] == initial["items"][0]
    assert after["plan"]["revision"] == 2
    assert client.get(f"/api/plans/{plan_id}/document").status_code == 200
    assert client.get(f"/api/plans/{plan_id}/document?revision=1").raise_for_status().text == historical


@pytest.mark.parametrize("fields", [
    {"title": " "},
    {"key": "first", "title": "Duplicate key"},
    {"title": "Invalid story reference", "story_keys": [None]},
])
def test_rejected_add_preserves_items_and_revision_history(fields):
    """Every proposed addition is validated before any item, revision number,
    digest, or retained document is changed."""
    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))
    plan = plans.create_draft(store, project, "Ship", [{"key": "first", "title": "First"}])
    before_items = plans.plan_items(store, plan)
    before_history = store.list(PlanRevision, plan_id=plan.id)
    before_document = document(store, plan)

    with pytest.raises(ValueError):
        plans.add_item(store, project, plan, fields, "human")

    assert store.get(Plan, plan.id) == plan
    assert plans.plan_items(store, plan) == before_items
    assert store.list(PlanRevision, plan_id=plan.id) == before_history
    assert document(store, plan) == before_document


@pytest.mark.parametrize("action", ["edit", "approve", "unapprove", "cancel"])
def test_stale_item_snapshot_cannot_change_started_work(action):
    """A snapshot read before acquiring the plan lock cannot authorize an
    operation after execution changes the stored item to in-flight."""
    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))
    plan = plans.create_draft(store, project, "Ship", [{"title": "First"}])
    proposed = plans.plan_items(store, plan)[0]
    plans.approve_all(store, plan)
    approved = store.get(PlanItem, proposed.id)
    plans.activate(store, project, plan)
    current = store.get(PlanItem, proposed.id)
    before = document(store, plan)

    with pytest.raises(ValueError):
        if action == "edit":
            plans.update_item(store, proposed, {"notes": "Changed after execution started"})
        elif action == "approve":
            plans.approve_item(store, plan, proposed)
        elif action == "unapprove":
            plans.unapprove_item(store, approved)
        else:
            plans.cancel_item(store, proposed)

    assert store.get(PlanItem, proposed.id) == current
    assert document(store, plan) == before


def test_stale_plan_snapshot_cannot_add_work_after_abandonment():
    """An old draft handle cannot add orphan proposals to a plan another
    caller already abandoned while the first caller waited for the lock."""
    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))
    plan = plans.create_draft(store, project, "Ship", [{"title": "First"}])
    plans.abandon_plan(store, plan)
    before = document(store, plan)

    with pytest.raises(ValueError, match="abandoned"):
        plans.add_item(store, project, plan, {"title": "Second"}, "human")

    assert document(store, plan) == before


def test_approved_spec_document_matches_the_retained_canonical_revision(app, monkeypatch):
    """Spec publication uses the same editable artifact as export, retaining
    shared context, baseline revisions, repositories, story keys, and prose."""
    client, _ = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    project = _api_project(client)
    source = render_plan_document({
        "goal": "  Ship the export\n",
        "context_documents": {"architecture.md": "## Existing choices\nKeep the storage interface."},
        "repo_baselines": {"https://github.com/o/r.git": "b" * 40},
        "items": [{"key": "csv", "title": "CSV export", "story_keys": ["export-unicode"],
                   "notes": "\n## Example\n```text\nname,city\nZoë,Zürich\n```\n"}],
    })
    accepted = client.post(f"/api/projects/{project}/plan/submit", json={"source": source, "start": True}).raise_for_status().json()
    plan_id = accepted["plan"]["id"]

    assert plans.PLAN_DOC_PATH == "iteration-plan.toml"
    published = FakeSpecRepo.commits[plans.PLAN_DOC_PATH]
    retained = client.get(f"/api/plans/{plan_id}/document?revision=1").raise_for_status().text
    assert published == retained
    assert accepted["plan"]["spec_ref"] == plans.PLAN_DOC_PATH
