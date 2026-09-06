"""The Inbox exposes every open action through a workspace-scoped HTTP API."""

from fastapi.testclient import TestClient

from hive.api import create_app
from hive.config.settings import Config
from hive._control.supervisor import Supervisor
from hive.models import HumanTask, Plan, PlanItem, Project, Question
from hive.persistence import MemoryStore
from tests.test_users_roles import login, make_app


def client_for(store):
    config = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
                    orch_model="", runner_token="test", data_dir=None)
    supervisor = Supervisor(store, lambda project, events: None)
    return TestClient(create_app(store, supervisor, config))


def test_inbox_paginates_all_actions_and_filters_without_losing_totals():
    """A large Inbox remains fully reachable, with stable counts across filters."""
    store = MemoryStore()
    client = client_for(store)
    project = store.put(Project(name="Search", spec_repo="https://example.com/search.git"))
    for index in range(27):
        store.put(Question(project_id=project.id, text=f"Question {index}", created_at=index + 1))
    store.put(Question(project_id=project.id, text="Already answered", status="answered"))
    store.put(HumanTask(title="Grant repository access", instructions="Enable the runner"))
    plan = store.put(Plan(project_id=project.id, goal="Search the catalog"))
    store.put(PlanItem(project_id=project.id, plan_id=plan.id, title="Index catalog"))
    active = store.put(Plan(project_id=project.id, goal="Repair search", status="approved"))
    store.put(PlanItem(project_id=project.id, plan_id=active.id, title="Choose matching semantics",
                       status="blocked_clarity", parked_reason="Exact or fuzzy?"))
    foreign = store.put(Project(workspace_id="foreign", name="Private"))
    store.put(Question(workspace_id="foreign", project_id=foreign.id, text="Secret"))

    first = client.get("/api/inbox?limit=20").json()
    assert first["total"] == 30
    assert len(first["items"]) == 20
    second = client.get("/api/inbox?limit=20&offset=20").json()
    assert len(second["items"]) == 10
    assert len({item["id"] for item in first["items"] + second["items"]}) == 30
    assert first["counts"] == {"all": 30, "question": 27, "todo": 1, "approval": 1,
                               "blocked": 1, "yours": 30, "others": 0}
    filtered = client.get("/api/inbox", params={"kind": "question", "q": "Question 2",
                                               "project_id": project.id}).json()
    assert filtered["total"] == 8
    assert filtered["counts"] == first["counts"]
    assert all(item["project_name"] == "Search" for item in filtered["items"])
    assert client.get("/api/inbox?limit=0").status_code == 422
    assert client.get("/api/inbox?kind=unknown").status_code == 422


def test_inbox_actions_preserve_owner_permissions_and_disappear_when_resolved():
    """Resource providers can clear their own todos while other work stays read-only."""
    store = MemoryStore()
    client, auth, _ = make_app(store)
    alice_headers, alice = login(auth, "alice")
    bob_headers, bob = login(auth, "bob")
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_headers)
    project = store.put(Project(name="Search", spec_repo="https://example.com/search.git"))
    question = store.put(Question(project_id=project.id, text="Exact or fuzzy?"))
    own = store.put(HumanTask(title="Login on your laptop", instructions="Run login", assignee_user_id=bob.id))
    other = store.put(HumanTask(title="Login on Alice's laptop", instructions="Run login", assignee_user_id=alice.id))

    inbox = client.get("/api/inbox", headers=bob_headers).json()
    assert inbox["counts"]["yours"] == 1
    assert inbox["counts"]["others"] == 2
    yours = client.get("/api/inbox?scope=yours", headers=bob_headers).json()
    assert [row["todo"]["id"] for row in yours["items"]] == [own.id]
    assert yours["items"][0]["can_act"] is True
    assert yours["items"][0]["assignee_name"] == "bob"
    assert all(not row["can_act"] for row in inbox["items"] if row["waiting_on_other"])
    assert client.post(f"/api/questions/{question.id}/answer", json={"answer": "Exact"}, headers=bob_headers).status_code == 403
    assert client.post(f"/api/human-todos/{other.id}/done", headers=bob_headers).status_code == 403
    assert client.post(f"/api/human-todos/{own.id}/done", headers=bob_headers).status_code == 200
    assert client.get("/api/inbox?scope=yours", headers=bob_headers).json()["total"] == 0
    assert client.post(f"/api/questions/{question.id}/answer", json={"answer": "Exact"}, headers=alice_headers).status_code == 200
    assert client.get("/api/inbox?kind=question", headers=alice_headers).json()["total"] == 0


def test_plan_history_keeps_finished_plans_addressable_and_scopes_to_project():
    """A later iteration must not hide the exact plan and evidence of earlier work."""
    store = MemoryStore()
    client = client_for(store)
    project = store.put(Project(name="Search", spec_repo="https://example.com/search.git"))
    older = store.put(Plan(project_id=project.id, goal="Catalog", status="complete", created_at=1))
    current = store.put(Plan(project_id=project.id, goal="Search", created_at=3))
    abandoned = store.put(Plan(project_id=project.id, goal="Retired approach", status="abandoned", created_at=2))
    foreign = store.put(Project(workspace_id="foreign", name="Private"))
    store.put(Plan(project_id=foreign.id, workspace_id="foreign", goal="Secret"))
    history = client.get(f"/api/projects/{project.id}/plans").json()["plans"]
    assert [plan["id"] for plan in history] == [current.id, abandoned.id, older.id]
    assert client.get(f"/api/plans/{older.id}").json()["plan"]["status"] == "complete"
    assert client.get(f"/api/projects/{foreign.id}/plans").status_code == 404


def test_inbox_plan_actions_only_include_live_review_and_blocked_items():
    """Approving amendments and retrying parked work clears the corresponding rows."""
    store = MemoryStore()
    client = client_for(store)
    project = store.put(Project(name="Search", spec_repo="https://example.com/search.git"))
    active = store.put(Plan(project_id=project.id, goal="Repair search", status="approved"))
    blocked = store.put(PlanItem(project_id=project.id, plan_id=active.id, title="Fix matching",
                                 status="rejected", parked_reason="Add coverage"))
    amendment = store.put(PlanItem(project_id=project.id, plan_id=active.id, title="Document matching"))
    past = store.put(Plan(project_id=project.id, goal="Superseded", status="abandoned"))
    store.put(PlanItem(project_id=project.id, plan_id=past.id, title="Old blocker", status="blocked_clarity"))
    store.put(Plan(project_id=project.id, goal="Empty draft"))

    rows = client.get("/api/inbox").json()["items"]
    assert {row["plan_item"]["id"] for row in rows} == {blocked.id, amendment.id}
    assert all(row["plan"]["id"] == active.id for row in rows)
    assert client.post(f"/api/plan-items/{amendment.id}/approve").status_code == 200
    assert client.get("/api/inbox?kind=approval").json()["total"] == 0
    assert client.post(f"/api/plan-items/{blocked.id}/retry").status_code == 200
    assert client.get("/api/inbox").json()["total"] == 0
