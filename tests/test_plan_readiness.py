"""Readiness is an observation of dispatch policy, never an approval or a probe."""

from test_plans import app as app, _api_project
from hive.models import Resource, ResourceUsability, Runner, Task


def test_readiness_reports_missing_reviewer_and_recovers_without_starting_work(app):
    """A usable builder is insufficient: owners see missing review capacity before approval."""
    client, store = app
    pid = _api_project(client)
    client.patch(f"/api/projects/{pid}", json={
        "build_backend": "claude", "review_backend": "codex", "validation_command": "make check",
    }).raise_for_status()
    plan = client.post(f"/api/projects/{pid}/plan/submit", json={
        "source": "# Export CSV\n\n## Export rows\nHandle Unicode.\n",
    }).raise_for_status().json()["plan"]
    runner = store.put(Runner(name="laptop", backends=["claude", "codex"]))
    store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable))
    path = f"/api/plans/{plan['id']}/readiness"
    response = client.get(path)
    assert response.status_code == 200, response.text
    checks = {check["key"]: check for check in response.json()["checks"]}
    assert checks["build"]["status"] == "ready"
    assert checks["review"]["status"] == "blocked"
    assert checks["review"]["action"] == "resources"
    assert checks["validation"]["detail"] == "make check"
    assert not response.json()["ready"]
    store.put(Resource(runner_id=runner.id, backend="codex", usability_status=ResourceUsability.usable))
    assert client.get(path).json()["ready"]
    assert client.get(f"/api/plans/{plan['id']}").json()["plan"]["status"] == "draft"
    assert store.list(Task) == []


def test_readiness_uses_dispatch_fallbacks_and_counts_sessions_already_started(app, monkeypatch):
    """The preview chooses the same usable model as dispatch; one session does not promise build AND review."""
    import time
    from test_plans import FakeSpecRepo
    from hive._control.supervisor import Supervisor
    from hive.models import Project
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    pid = _api_project(client)
    client.patch(f"/api/projects/{pid}", json={
        "build_backend": "claude", "build_model": "cooling-model", "included_only": True,
        "agent_preferences": [{"backend": "claude", "model": "available-model"}],
        "agent_grants": [{"backends": ["claude"], "sessions_per_day": 1}],
    }).raise_for_status()
    plan = client.post(f"/api/projects/{pid}/plan/submit", json={
        "source": "# Export CSV\n\n## Export rows\nHandle Unicode.\n",
    }).raise_for_status().json()["plan"]
    runner = store.put(Runner(name="laptop", backends=["claude"]))
    store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable,
                       model_cooldowns={"cooling-model": time.time() + 600}))
    path = f"/api/plans/{plan['id']}/readiness"
    snapshot = client.get(path).raise_for_status().json()
    assert snapshot["ready"]
    assert "available-model" in next(c for c in snapshot["checks"] if c["key"] == "build")["detail"]
    client.post(f"/api/plans/{plan['id']}/approve").raise_for_status()
    supervisor = Supervisor(store, lambda pid, events: None)
    assert supervisor.dispatch(store.get(Project, pid)) == 1
    task = client.get(f"/api/plans/{plan['id']}").json()["tasks"][0]
    assert task["model"] == "available-model"
    checks = {c["key"]: c for c in client.get(path).json()["checks"]}
    assert checks["build"]["status"] == "ready"  # running work is not stopped retroactively
    assert checks["review"]["status"] == "blocked"
    assert "allowance" in checks["review"]["detail"]
    assert checks["review"]["action"] == "settings"


def test_readiness_preserves_review_retry_affinity_and_suppresses_finished_build(app):
    """Review transport retries need their original checkout, even with other healthy runners."""
    import time
    from hive.models import Plan, PlanItem, PlanItemStatus, PlanStatus, TaskKind
    client, store = app
    pid = _api_project(client)
    client.patch(f"/api/projects/{pid}", json={"build_backend": "codex", "review_backend": "claude"}).raise_for_status()
    plan = store.put(Plan(project_id=pid, goal="Export rows", status=PlanStatus.approved))
    item = store.put(PlanItem(project_id=pid, plan_id=plan.id, title="Export", status=PlanItemStatus.reviewing))
    owner = store.put(Runner(name="sleeping laptop", backends=["claude"], last_seen=time.time() - 600))
    other = store.put(Runner(name="other machine", backends=["claude"]))
    for runner in (owner, other):
        store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable))
    store.put(Task(project_id=pid, workstream_id=item.id, work_item_id=item.id, run_id=plan.id,
                   repo="https://github.com/o/r.git", kind=TaskKind.review, backend="claude",
                   instructions="Review exported rows", preserve_checkout=True, resume_runner_id=owner.id))
    path = f"/api/plans/{plan.id}/readiness"
    snapshot = client.get(path).raise_for_status().json()
    checks = {c["key"]: c for c in snapshot["checks"]}
    assert checks["build"]["status"] == "info"
    assert checks["review"]["status"] == "blocked"
    assert "offline" in checks["review"]["detail"]
    assert "unfinished checkout" in checks["review"]["detail"]
    store.update(Runner, owner.id, lambda r: setattr(r, "last_seen", time.time()))
    assert client.get(path).json()["ready"]


def test_cli_readiness_distinguishes_amendment_approval_and_policy_blockers(app):
    """An amendment at the head cannot execute until approved; paused settings are actionable."""
    from test_cli import cli
    from hive.cli import format_readiness
    from hive.models import Plan, PlanItem, PlanStatus
    client, store = app
    pid = _api_project(client)
    client.patch(f"/api/projects/{pid}", json={"paused": True, "daily_budget_usd": 0}).raise_for_status()
    plan = store.put(Plan(project_id=pid, goal="Export rows", status=PlanStatus.approved))
    store.put(PlanItem(project_id=pid, plan_id=plan.id, title="Amended export"))
    response = cli(client, "plan-ready", plan.id)
    checks = {c["key"]: c for c in response["checks"]}
    assert not response["ready"]
    assert checks["plan"]["status"] == "blocked"
    assert "approv" in checks["plan"]["detail"].lower()
    assert checks["project_pause"]["action"] == "resume_project"
    assert checks["budget"]["action"] == "settings"
    human = format_readiness(response)
    assert "BLOCKED" in human and "Amended export" in human
    assert "not reserve" in human


def test_capacity_waits_for_conflicting_work_but_allows_parallel_test_runs(app, monkeypatch):
    """The read-side and dispatcher agree on workspace occupancy, including parallel test tasks."""
    from test_plans import FakeSpecRepo
    from hive._control.supervisor import Supervisor
    from hive.models import Project, TaskKind, TaskStatus
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    pid = _api_project(client)
    client.patch(f"/api/projects/{pid}", json={"build_backend": "claude"}).raise_for_status()
    worker = store.put(Runner(name="builder", backends=["claude"]))
    store.put(Resource(runner_id=worker.id, backend="claude", usability_status=ResourceUsability.usable))
    busy = store.put(Task(project_id="another-project", workstream_id="test", repo="https://github.com/o/r.git",
                          runner_id="test-runner", kind=TaskKind.resolve, backend="claude", instructions="other work",
                          status=TaskStatus.running))
    plan = client.post(f"/api/projects/{pid}/plan/submit", json={
        "source": "# Export CSV\n\n## Export rows\nHandle Unicode.\n", "start": True,
    }).raise_for_status().json()["plan"]
    path = f"/api/plans/{plan['id']}/readiness"
    supervisor = Supervisor(store, lambda pid, events: None)
    checks = {c["key"]: c for c in client.get(path).json()["checks"]}
    assert checks["build"]["status"] == "waiting"
    assert supervisor.dispatch(store.get(Project, pid)) == 0
    store.update(Task, busy.id, lambda t: setattr(t, "kind", TaskKind.test_sweep))
    assert client.get(path).json()["ready"]
    assert supervisor.dispatch(store.get(Project, pid)) == 1
