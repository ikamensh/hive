"""Subscription fallback through real plan dispatch and task-result handling."""

import time

from hive._control.supervisor import Supervisor
from hive.models import AgentPreference, PlanItemStatus, Project, Resource, ResourceUsability, Runner, Task, TaskStatus
from hive.persistence.store import MemoryStore
from hive.runner._task_results import TaskResult
from hive._workstreams import plans
from test_plans import make_processor


PREFERENCES = [
    AgentPreference(backend="codex", model="gpt-6-astra"),
    AgentPreference(backend="claude", model="claude-fable-5-1"),
    AgentPreference(backend="claude", model="claude-opus-5"),
    AgentPreference(backend="opencode", model="opencode/muse-spark-1.3-contributor-free"),
]


def test_interrupted_plan_uses_each_subscription_then_free_capacity(tmp_path):
    """Attempts keep one checkout and one item, clear incompatible session IDs, then finish review."""
    store = MemoryStore()
    project = store.put(Project(name="simulator", spec_repo="https://github.com/o/simulator.git",
                                included_only=True, daily_budget_usd=0, agent_preferences=PREFERENCES))
    runner = store.put(Runner(name="laptop", backends=["codex", "claude", "opencode"]))
    for backend in runner.backends:
        store.put(Resource(runner_id=runner.id, backend=backend, usability_status=ResourceUsability.usable))
    plan = plans.create_draft(store, project, "Build a simulator", [{"title": "Cycle model"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    supervisor = Supervisor(store, lambda *_: None)
    processor, _ = make_processor(store, tmp_path)
    attempts = []
    for expected in PREFERENCES:
        assert supervisor.dispatch(project) == 1
        task = store.list(Task, status=TaskStatus.running)[0]
        attempts.append(task)
        assert (task.backend, task.model) == (expected.backend, expected.model)
        assert task.session_handle == ""
        if len(attempts) > 1:
            assert task.preserve_checkout and not task.fresh_branch
            assert task.resume_runner_id == runner.id
            assert task.branch == attempts[0].branch
        if expected.backend == "opencode":
            processor.handle(task.id, TaskResult(text="Implemented\nOUTCOME: FIXED"), task.workspace_id)
            break
        # Claude's scoped cap must not cool the other model; Codex's message is shared.
        text = "Fable weekly limit reached" if "fable" in expected.model else "Opus weekly limit reached"
        if expected.backend == "codex":
            text = "You've hit your usage limit"
        processor.handle(task.id, TaskResult(text=text, is_error=True, resource_exhausted=True,
                                            reset_at_hint=time.time()+3600, session_handle=f"session-{task.id}"),
                         task.workspace_id)
    assert len({t.id for t in attempts}) == 4
    assert supervisor.dispatch(project) == 1
    reviewer = store.list(Task, status=TaskStatus.running)[0]
    assert reviewer.backend == "opencode" and reviewer.session_handle == ""
    processor.handle(reviewer.id, TaskResult(text="Checked independently\nREVIEW: ACCEPT"), reviewer.workspace_id)
    assert plans.plan_items(store, plan)[0].status == PlanItemStatus.done
    assert [(store.get(Task, t.id).backend, store.get(Task, t.id).model) for t in attempts] == [
        (p.backend, p.model) for p in PREFERENCES
    ]


def test_status_and_dispatch_obey_scoped_limits_grants_and_recovery(tmp_path):
    """A waiting policy reports the same capacity it will dispatch, including per-model limits."""
    from hive.models import AgentGrant, ProjectState, UsageWindow

    store = MemoryStore()
    project = store.put(Project(name="p", included_only=True, daily_budget_usd=0,
                                agent_preferences=PREFERENCES,
                                agent_grants=[AgentGrant(backends=["claude"], models=["claude-opus-5"], sessions_per_day=1)]))
    runner = store.put(Runner(name="laptop", backends=["claude"]))
    resource = store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable,
                                  usage_windows=[UsageWindow(kind="weekly_fable", model_scope="fable",
                                                             used_percent=100, resets_at=time.time()+7200)]))
    task = store.put(Task(project_id=project.id, workstream_id="w", repo="r", instructions="work",
                          backend="codex", model="gpt-6-astra"))
    supervisor = Supervisor(store, lambda *_: None)
    assert supervisor.refresh_state(project) == ProjectState.working
    assert supervisor.dispatch(project) == 1
    assert store.get(Task, task.id).model == "claude-opus-5"
    store.update(Task, task.id, lambda t: setattr(t, "status", TaskStatus.done))
    store.put(Task(project_id=project.id, workstream_id="w", repo="r", instructions="next",
                   backend="codex", model="gpt-6-astra"))
    assert supervisor.refresh_state(project) == ProjectState.blocked_budget
    assert supervisor.dispatch(project) == 0
    store.update(Project, project.id, lambda p: setattr(p, "agent_grants", []))
    project = store.get(Project, project.id)
    store.update(Resource, resource.id, lambda r: r.usage_windows.append(
        UsageWindow(kind="session", used_percent=100, resets_at=time.time()+3600)))
    assert supervisor.refresh_state(project) == ProjectState.blocked_resources
    assert supervisor.dispatch(project) == 0
    store.update(Resource, resource.id, lambda r: setattr(r, "usage_windows", []))
    assert supervisor.refresh_state(project) == ProjectState.working
    assert supervisor.dispatch(project) == 1
    assert store.list(Task, status=TaskStatus.running)[0].model == "claude-fable-5-1"


def test_intake_fallback_preserves_context_without_cross_provider_session(tmp_path):
    """A scout fallback updates durable conversation identity and subsequent turns stay resumable."""
    from hive._control.intake import queue_turn
    from hive.models import AgentConversation, ConversationStatus

    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://github.com/o/r.git", agent_preferences=PREFERENCES))
    runner = store.put(Runner(name="laptop", backends=["claude"]))
    store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable))
    conversation = store.put(AgentConversation(project_id=project.id, repo=project.spec_repo,
                                               backend="codex", model="gpt-6-astra", session_handle="codex-session",
                                               latest_brief="Use deterministic physics"))
    queue_turn(store, project, conversation, "message", "Include energy accounting")
    supervisor = Supervisor(store, lambda *_: None)
    assert supervisor.dispatch(project) == 1
    task = store.list(Task, status=TaskStatus.running)[0]
    assert "deterministic physics" in task.instructions
    assert "energy accounting" in task.instructions
    assert task.session_handle == "" and task.model == "claude-fable-5-1"
    running_conversation = store.get(AgentConversation, conversation.id)
    assert running_conversation.backend == "claude" and running_conversation.session_handle == ""
    processor, _ = make_processor(store, tmp_path)
    processor.handle(task.id, TaskResult(text="Updated simulation brief", session_handle="claude-session"), task.workspace_id)
    updated = store.get(AgentConversation, conversation.id)
    assert updated.status == ConversationStatus.open
    assert updated.backend == "claude" and updated.model == "claude-fable-5-1"
    queue_turn(store, project, updated, "message", "Proceed")
    assert supervisor.dispatch(project) == 1
    assert store.list(Task, status=TaskStatus.running)[0].session_handle == "claude-session"


def test_dispatch_records_why_preferred_model_was_skipped():
    """Actual selection explains a fallback and does not rewrite the explanation once running."""
    store = MemoryStore()
    project = store.put(Project(name="p", agent_preferences=PREFERENCES))
    runner = store.put(Runner(name="laptop", backends=["codex", "claude"]))
    store.put(Resource(runner_id=runner.id, backend="codex", usability_status=ResourceUsability.probing))
    store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable))
    task = store.put(Task(project_id=project.id, workstream_id="w", repo="r", instructions="x"))
    supervisor = Supervisor(store, lambda *_: None)
    assert supervisor.dispatch(project) == 1
    chosen = store.get(Task, task.id)
    assert chosen.dispatch_reason == (
        "selected claude=claude-fable-5-1; codex=gpt-6-astra: laptop: probing"
    )
    assert supervisor.dispatch(project) == 0
    assert store.get(Task, task.id).dispatch_reason == chosen.dispatch_reason


def test_intake_quota_or_flake_creates_fresh_attempt_and_rejects_late_results(tmp_path):
    """Switching a failed intake attempt to Muse preserves the brief and isolates provider ledgers."""
    from hive._control.intake import queue_turn
    from hive.models import AgentConversation, ConversationStatus

    for quota in (False, True):
        store = MemoryStore()
        project = store.put(Project(name="p", spec_repo="https://github.com/o/r.git",
                                    agent_preferences=[PREFERENCES[1], PREFERENCES[-1]]))
        runner = store.put(Runner(name="laptop", backends=["claude", "opencode"]))
        claude = store.put(Resource(runner_id=runner.id, backend="claude", usability_status=ResourceUsability.usable))
        store.put(Resource(runner_id=runner.id, backend="opencode", usability_status=ResourceUsability.usable))
        conversation = store.put(AgentConversation(project_id=project.id, repo=project.spec_repo,
                                                   backend="claude", model=PREFERENCES[1].model,
                                                   latest_brief="Deterministic physics"))
        original = queue_turn(store, project, conversation, "message", "Include energy accounting")
        supervisor = Supervisor(store, lambda *_: None)
        processor, _ = make_processor(store, tmp_path)
        processor.supervisor = supervisor
        assert supervisor.dispatch(project) == 1
        processor.handle(original.id, TaskResult(
            text="Fable weekly limit reached" if quota else "Internal server error", is_error=True,
            resource_exhausted=quota, reset_at_hint=time.time()+3600 if quota else 0,
            input_tokens=1000, session_handle="fable-session",
        ), project.workspace_id)
        store.update(Resource, claude.id, lambda r: setattr(r, "cooldown_until", time.time()+3600))
        assert store.get(AgentConversation, conversation.id).status == ConversationStatus.running
        assert supervisor.dispatch(project) == 1
        successor = store.list(Task, status=TaskStatus.running)[0]
        assert successor.id != original.id
        assert successor.retry_of_task_id == original.id
        assert successor.backend == "opencode" and successor.input_tokens == 0
        assert successor.preserve_checkout and successor.resume_runner_id == runner.id
        assert successor.session_handle == ""
        assert store.get(AgentConversation, conversation.id).last_task_id == successor.id
        late = processor.handle(original.id, TaskResult(text="Stale brief", input_tokens=50), project.workspace_id)
        assert late["ignored"]
        assert store.get(Task, successor.id).status == TaskStatus.running
        assert store.get(AgentConversation, conversation.id).latest_brief == "Deterministic physics"
        processor.handle(successor.id, TaskResult(text="New brief", input_tokens=200), project.workspace_id)
        assert store.get(AgentConversation, conversation.id).latest_brief == "New brief"
        assert store.get(Task, original.id).input_tokens == 1000
        assert store.get(Task, successor.id).input_tokens == 200


def test_interrupted_attempt_recovers_after_store_restart_without_relinking_a_newer_turn(tmp_path):
    """Failure recording may precede a chief crash; recovery is idempotent and cannot reopen old work."""
    from concurrent.futures import ThreadPoolExecutor
    from hive._control.retries import resume_interrupted_task
    from hive.models import AgentConversation, ConversationStatus
    from hive.persistence.store import FileStore

    store = FileStore(tmp_path / "store")
    task = store.put(Task(project_id="p", workstream_id="", repo="r", instructions="continue",
                          kind="intake", backend="claude", model=PREFERENCES[1].model,
                          status=TaskStatus.failed, runner_id="laptop", retryable_interruption=True,
                          input_tokens=1234, result_text="rate limit"))
    conversation = store.put(AgentConversation(project_id="p", repo="r", backend="claude",
                                               status=ConversationStatus.running, last_task_id=task.id))
    store.update(Task, task.id, lambda t: setattr(t, "conversation_id", conversation.id))
    store = FileStore(tmp_path / "store")
    with ThreadPoolExecutor(max_workers=2) as pool:
        retries = list(pool.map(lambda _: resume_interrupted_task(store, task), range(2)))
    assert retries[0].id == retries[1].id
    assert len(store.list(Task)) == 2
    assert store.get(AgentConversation, conversation.id).last_task_id == retries[0].id
    assert retries[0].input_tokens == 0 and store.get(Task, task.id).input_tokens == 1234
    store.update(AgentConversation, conversation.id, lambda c: setattr(c, "status", ConversationStatus.open))
    assert resume_interrupted_task(store, task) is None
    assert store.get(AgentConversation, conversation.id).status == ConversationStatus.open
