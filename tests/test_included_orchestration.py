"""Included-only planning uses the real chief and CLI process with no API spend."""

import asyncio
import os
import time

import httpx
import pytest

from hive._control.orchestrator import Orchestrator
from hive._control.supervisor import Supervisor
from hive.api import create_app, make_todo_triage
from hive._control.escalation import escalate
from hive.models import (
    AgentConversation, AgentPreference, ConversationStatus, HumanTask, OrchestratorRun, Plan,
    PlanItem, Project, Resource, ResourceUsability, Runner, Task,
)
from hive._workstreams import plans
from hive.persistence.blobstore import LocalBlobStore
from hive.persistence.store import MemoryStore
from test_llm import _config
from test_opencode_llm import executable


def chief(tmp_path, monkeypatch, **config_overrides):
    class Spec:
        def __init__(self, *args):
            pass

        def sync(self):
            pass

        def digest(self):
            return "Build a production simulator."

    monkeypatch.setattr("hive._control.orchestrator.SpecRepo", Spec)
    config = _config(data_dir=tmp_path, **config_overrides)
    store = MemoryStore()
    project = store.put(Project(name="simulator", spec_repo="https://example.com/sim.git",
                                included_only=True, daily_budget_usd=0))
    conversation = store.put(AgentConversation(project_id=project.id, repo=project.spec_repo,
                                                backend="opencode", status=ConversationStatus.done))
    project.intake_conversation_id = conversation.id
    store.put(project)
    blobs = LocalBlobStore(tmp_path / "blobs")
    orchestrator = Orchestrator(store, blobs, config)
    supervisor = Supervisor(store, orchestrator.invoke)
    app = create_app(store, supervisor, config, blobs)
    return app, store, project, supervisor, orchestrator, config


def test_zero_budget_included_project_can_request_free_plan(tmp_path, monkeypatch):
    """The HTTP request reaches a free CLI planner through the scheduler,
    creates an unapproved draft, and leaves both spend and runnable work zero."""
    executable(tmp_path, '''
import json, sys
request = json.load(sys.stdin)
if any(message["role"] == "tool" for message in request["messages"]):
    turn = {"text": "Draft ready for review.", "tool_calls": []}
else:
    turn = {"text": "", "tool_calls": [{"name": "propose_plan", "arguments": {
        "goal": "Production simulation", "items_json": json.dumps([{"title": "Model the line"}])}}]}
print(json.dumps({"type": "text", "part": {"text": json.dumps(turn)}}))
print(json.dumps({"type": "step_finish", "part": {"tokens": {"input": 100, "output": 20}}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    app, store, project, supervisor, _, _ = chief(tmp_path, monkeypatch, orch_provider="opencode")
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/projects/{project.id}/plan/propose")
            assert response.status_code == 200, response.text
            await supervisor._step()
            async with asyncio.timeout(5):
                while not store.list(OrchestratorRun, project_id=project.id):
                    await asyncio.sleep(0.01)
            response = await client.get(f"/api/projects/{project.id}")
            assert response.status_code == 200, response.text

    asyncio.run(exercise())
    assert len(store.list(Plan, project_id=project.id)) == 1
    assert store.list(Plan, project_id=project.id)[0].status == "draft"
    assert store.list(PlanItem, project_id=project.id)[0].title == "Model the line"
    assert store.list(Task, project_id=project.id) == []
    run = store.list(OrchestratorRun, project_id=project.id)[0]
    assert run.model == "opencode/muse-spark-1.3-contributor-free"
    assert run.input_tokens == 200 and run.output_tokens == 40
    assert run.cost_usd == supervisor.spend_today(project.id) == 0


@pytest.mark.parametrize("options", [
    {"orch_provider": "auto", "openai_api_key": "configured", "gemini_api_key": "configured"},
    {"orch_provider": "openai", "orch_model": "opencode/test-free", "openai_api_key": "configured"},
    {"orch_provider": "gemini", "orch_model": "opencode/test-free", "gemini_api_key": "configured"},
    {"orch_provider": "opencode", "orch_model": "openai/gpt-6-astra"},
    {"orch_provider": "auto", "orch_model": "opencode/paid-model"},
])
def test_included_policy_blocks_paid_planning_at_every_entry(tmp_path, monkeypatch, options):
    """API, scheduler and direct invocations reject any configuration that
    could select a paid provider, even when its model name looks free."""
    app, store, project, supervisor, orchestrator, config = chief(tmp_path, monkeypatch, **options)
    stale = escalate(store, "Check completed task", "The task finished.", project_id=project.id)

    def forbidden(*args):
        pytest.fail("Included-only policy instantiated a paid-capable adapter")

    for provider in ("OpenAIAdapter", "GeminiAdapter", "OpenCodeAdapter"):
        monkeypatch.setattr(f"hive.llm._provider.{provider}", forbidden)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/projects/{project.id}/plan/propose")
            assert response.status_code == 400, response.text
        supervisor.wake(project.id, "A task finished; consider the next plan")
        await supervisor._step()
        assert project.id not in supervisor._busy

    asyncio.run(exercise())
    with pytest.raises(ValueError, match="included-only"):
        orchestrator.invoke(project.id, ["Direct request"])
    make_todo_triage(store, config)()
    assert store.get(HumanTask, stale.id).status == "open"
    assert store.list(Plan) == store.list(OrchestratorRun) == []


@pytest.mark.parametrize("selection", [
    {"orch_provider": "opencode"},
    {"orch_provider": "auto", "orch_model": "opencode/test-free"},
])
def test_included_todo_triage_uses_only_selected_free_cli(tmp_path, monkeypatch, selection):
    """Workspace triage remains available with only included-only/$0 projects,
    even when paid API credentials happen to exist in the chief config."""
    executable(tmp_path, '''
import json, os, sys
request = json.load(sys.stdin)
assert os.environ["STALE_TODO"] in request["messages"][-1]["content"]
answer = {"decisions": [{"todo_id": os.environ["STALE_TODO"], "verdict": "stale", "reason": "The task finished."}]}
print(json.dumps({"type": "text", "part": {"text": json.dumps(answer)}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    _, store, project, _, _, config = chief(tmp_path, monkeypatch, **selection,
                                           openai_api_key="configured", gemini_api_key="configured")
    stale = escalate(store, "Check completed task", "The task finished.", project_id=project.id)
    monkeypatch.setenv("STALE_TODO", stale.id)

    def forbidden(*args):
        pytest.fail("Free triage instantiated a paid API adapter")

    for provider in ("OpenAIAdapter", "GeminiAdapter"):
        monkeypatch.setattr(f"hive.llm._provider.{provider}", forbidden)
    make_todo_triage(store, config)()
    assert store.get(HumanTask, stale.id).status == "done"
    assert store.get(HumanTask, stale.id).resolved_reason == "triage: The task finished."


@pytest.mark.parametrize("selection", [
    {"orch_provider": "opencode"},
    {"orch_provider": "auto", "orch_model": "opencode/test-free"},
])
def test_free_planner_quota_never_falls_back_to_paid_credentials(tmp_path, monkeypatch, selection):
    """Free-tier unavailability propagates visibly without constructing an
    API adapter or producing a misleading successful planner run."""
    from hive.llm import ProviderUnavailable

    executable(tmp_path, '''
import json, sys
print(json.dumps({"type": "error", "error": {"data": {"statusCode": 429, "message": "Free quota reached"}}}))
sys.exit(1)
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    _, store, project, _, orchestrator, config = chief(tmp_path, monkeypatch, **selection,
                                                      openai_api_key="configured", gemini_api_key="configured")

    def forbidden(*args):
        pytest.fail("Free quota exhaustion constructed a paid API fallback")

    for provider in ("OpenAIAdapter", "GeminiAdapter"):
        monkeypatch.setattr(f"hive.llm._provider.{provider}", forbidden)
    with pytest.raises(ProviderUnavailable, match="Free quota reached"):
        orchestrator.invoke(project.id, ["Propose a plan"])
    todo = escalate(store, "Check completed task", "The task finished.", project_id=project.id)
    with pytest.raises(ProviderUnavailable, match="Free quota reached"):
        make_todo_triage(store, config)()
    assert store.get(HumanTask, todo.id).status == "open"
    assert store.list(Plan) == store.list(OrchestratorRun) == []


def test_restart_waits_for_plan_capacity_without_replanning(tmp_path, monkeypatch):
    """A free-enabled chief restart preserves an approved plan through scoped
    quota exhaustion. Restored Muse capacity runs the same queued task, while
    an explicit human event still reaches the planner during execution."""
    _, store, project, supervisor, _, _ = chief(tmp_path, monkeypatch, orch_provider="opencode")
    project.agent_preferences = [
        AgentPreference(backend="claude", model="claude-fable-5-1"),
        AgentPreference(backend="claude", model="claude-opus-5"),
        AgentPreference(backend="opencode", model="opencode/muse-spark-1.3-contributor-free"),
    ]
    store.put(project)
    runner = store.put(Runner(name="laptop", backends=["claude", "opencode"]))
    later = time.time() + 3600
    store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable,
                       model_cooldowns={"fable": later, "opus": later}))
    muse = store.put(Resource(runner_id=runner.id, backend="opencode",
                              usability_status=ResourceUsability.usable, cooldown_until=later))
    plan = plans.create_draft(store, project, "Arithmetic package", [
        {"title": "Addition"}, {"title": "Multiplication"},
    ])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    original = store.list(Task, project_id=project.id)[0]
    original_items = [item.model_dump() for item in plans.plan_items(store, plan)]
    calls = []
    supervisor.orchestrate = lambda pid, events: calls.append((pid, events))

    async def tick():
        await supervisor._step()
        callbacks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        await asyncio.gather(*callbacks)

    asyncio.run(tick())
    assert store.get(Project, project.id).state == "blocked_resources"
    assert not calls, "quota waiting must not ask the planner to replace or amend approved work"
    assert [item.model_dump() for item in plans.plan_items(store, plan)] == original_items
    assert [task.id for task in store.list(Task, project_id=project.id)] == [original.id]

    store.update(Resource, muse.id, lambda resource: setattr(resource, "cooldown_until", 0))
    asyncio.run(tick())
    current = store.get(Task, original.id)
    assert current.status == "running" and current.backend == "opencode"
    assert current.work_item_id == original.work_item_id and not calls

    supervisor.wake(project.id, "The human requests a progress explanation.")
    asyncio.run(tick())
    assert calls == [(project.id, ["The human requests a progress explanation."])]


def test_fleet_pause_reason_reaches_project_and_plan_views(tmp_path, monkeypatch):
    """Fleet pause explains queued work consistently in project and CLI plan
    views; pausing after dispatch describes draining without stopping work."""
    from hive.cli import format_plan

    app, store, project, supervisor, _, _ = chief(tmp_path, monkeypatch, orch_provider="opencode")
    project.build_backend = "opencode"
    store.put(project)
    runner = store.put(Runner(name="laptop", backends=["opencode"]))
    store.put(Resource(runner_id=runner.id, backend="opencode", usability_status=ResourceUsability.usable))
    plan = plans.create_draft(store, project, "Arithmetic", [{"title": "Addition"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    supervisor.refresh_state(project)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.patch("/api/workspace", json={"paused": True})).status_code == 200
            assert supervisor.dispatch(project) == 0
            detail = (await client.get(f"/api/projects/{project.id}")).json()
            assert "fleet paused" in detail["state_reason"]
            assert "resume" in detail["state_reason"]
            assert detail["plan"]["state_reason"] == detail["state_reason"]
            assert detail["state_reason"] in format_plan(detail["plan"])
            assert (await client.patch("/api/workspace", json={"paused": False})).status_code == 200
            assert supervisor.dispatch(project) == 1
            assert (await client.patch("/api/workspace", json={"paused": True})).status_code == 200
            detail = (await client.get(f"/api/projects/{project.id}")).json()
            assert "draining 1" in detail["state_reason"]
            assert detail["plan"]["state_reason"] == detail["state_reason"]
            assert store.list(Task, project_id=project.id)[0].status == "running"

    asyncio.run(exercise())
