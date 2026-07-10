"""Per-project agent allowances: grants, accounting, and the gates.

The promises tested here (design: wiki/agent-allowances.md):
- accounting is a pure recompute over today's tasks — sessions land on the
  grant with the most headroom, unlimited grants absorbing what they match so
  limited "any" capacity survives for tasks nothing else covers;
- dispatch is the hard gate (a disallowed pending task waits, per task, not
  per project) and exempt kinds (probe/preflight) never consume a session;
- the planner and the deterministic pipelines are steered at creation:
  create_task rejects disallowed pairs, pipelines remap their configured
  default onto a permitted pair.
"""

import time

import pytest
from fastapi import HTTPException

from hive._control.allowances import (
    admits,
    allowance_view,
    grant_problems,
    permitted,
    remaining,
    resolve_agent,
    sessions_today,
)
from hive._control.intake import TRUSTED_SCOUTS, trusted_capacity
from hive._control.orchestrator import Tools
from hive._control.supervisor import Supervisor, compute_state
from hive._workstreams.issues import advance_issues, reconcile
from hive._workstreams.testing import queue_refresh_task, start_episode
from hive.models import (
    AgentGrant,
    Project,
    ProjectState,
    ProjectWorkstream,
    ProjectWorkstreamKind,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
    Workstream,
)
from hive.persistence.store import MemoryStore

CHEAP = AgentGrant(backends=["codex"], models=["gpt-5.4-mini"])
ANY_5 = AgentGrant(sessions_per_day=5)


def started_task(backend: str, model: str = "", kind: TaskKind = TaskKind.work) -> Task:
    return Task(
        project_id="p", workstream_id="w", repo="r", instructions="i",
        backend=backend, model=model, kind=kind,
        status=TaskStatus.done, started_at=time.time(),
    )


# -- pure accounting ---------------------------------------------------------


def test_matching_wildcards_and_model_restriction():
    """Empty lists are wildcards; a model-restricted grant does not cover the
    backend-default (empty) model — restricted tasks must be concrete."""
    assert permitted([ANY_5], "cursor", "")
    assert permitted([CHEAP], "codex", "gpt-5.4-mini")
    assert not permitted([CHEAP], "codex", "")
    assert not permitted([CHEAP], "codex", "gpt-5.5")
    assert not permitted([CHEAP], "cursor", "gpt-5.4-mini")
    assert permitted([], "anything", "at-all")  # no grants = no limits


def test_unlimited_grant_absorbs_its_sessions():
    """The 5-any + unlimited-cheap combination: cheap sessions must never
    deplete the limited any-grant, however many of them ran."""
    grants = [ANY_5, CHEAP]
    sessions = [started_task("codex", "gpt-5.4-mini") for _ in range(40)]
    assert remaining(grants, sessions) == [5, None]
    # ...and expensive sessions consume only the any-grant.
    sessions += [started_task("cursor") for _ in range(2)]
    assert remaining(grants, sessions) == [3, None]


def test_remaining_goes_negative_on_overconsumption():
    """Tightening grants mid-day must not forget sessions already run: the
    recompute shows negative headroom and admits() stays False."""
    grants = [AgentGrant(sessions_per_day=1)]
    sessions = [started_task("cursor"), started_task("cursor")]
    left = remaining(grants, sessions)
    assert left == [-1]
    assert not admits(grants, left, "cursor", "")


def test_admits_is_per_pair_not_per_project():
    """An exhausted any-grant blocks expensive pairs while the cheap pair,
    covered by its own unlimited grant, stays admitted."""
    grants = [AgentGrant(sessions_per_day=1), CHEAP]
    left = remaining(grants, [started_task("cursor")])
    assert not admits(grants, left, "cursor", "")
    assert admits(grants, left, "codex", "gpt-5.4-mini")


def test_exempt_kinds_never_consume():
    """Probes and preflights are health checks, not agent sessions."""
    probe = started_task("cursor", kind=TaskKind.probe)
    preflight = started_task("codex", kind=TaskKind.preflight)
    assert sessions_today([probe, preflight], day_start=0.0) == []


def test_resolve_agent_prefers_the_preference_then_the_first_grant():
    """A permitted preference passes through untouched; a disallowed one is
    remapped onto the first grant, keeping whatever the grant doesn't pin."""
    assert resolve_agent([], "cursor", "") == ("cursor", "")
    assert resolve_agent([ANY_5, CHEAP], "cursor", "") == ("cursor", "")
    assert resolve_agent([CHEAP], "cursor", "") == ("codex", "gpt-5.4-mini")
    assert resolve_agent([CHEAP], "codex", "gpt-5.5") == ("codex", "gpt-5.4-mini")
    # A backend-only grant keeps the caller's model choice.
    only_gemini = AgentGrant(backends=["gemini-cli"])
    assert resolve_agent([only_gemini], "codex", "m1") == ("gemini-cli", "m1")


def test_grant_validation_catches_typos():
    assert "unknown backend" in grant_problems([AgentGrant(backends=["kodex"])], ["codex"])
    assert "sessions_per_day" in grant_problems([AgentGrant(sessions_per_day=-1)], ["codex"])
    assert grant_problems([CHEAP, ANY_5], ["codex"]) == ""


def test_allowance_view_reports_headroom():
    view = allowance_view([ANY_5, CHEAP], [started_task("cursor")])
    assert view["limited"] and view["sessions_today"] == 1
    assert [g["remaining_today"] for g in view["grants"]] == [4, None]
    assert "4/5 left today" in view["summary"]


# -- supervisor: dispatch gate and project state ------------------------------


def seed_fleet(store, backends=("cursor",), runners=2):
    ids = []
    for i in range(runners):
        runner = store.put(Runner(name=f"r{i}", backends=list(backends)))
        for backend in backends:
            store.put(Resource(runner_id=runner.id, backend=backend,
                               usability_status=ResourceUsability.usable))
        ids.append(runner.id)
    return ids


def pending_task(store, project, repo, backend="cursor", model="", kind=TaskKind.work):
    return store.put(Task(project_id=project.id, workstream_id="w", repo=repo,
                          instructions="i", backend=backend, model=model, kind=kind))


def test_dispatch_stops_at_the_session_cap():
    """Two capable runners, two pending tasks on distinct repos, but a 1/day
    grant: exactly one task starts; the other waits for the midnight reset."""
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="s",
                                agent_grants=[AgentGrant(sessions_per_day=1)]))
    seed_fleet(store)
    pending_task(store, project, "https://example.com/a.git")
    pending_task(store, project, "https://example.com/b.git")
    sup = Supervisor(store, orchestrate=lambda pid, events: None)
    assert sup.dispatch(project) == 1
    assert sup.dispatch(project) == 0  # recomputed from store facts, still capped


def test_dispatch_lets_granted_pairs_through_an_exhausted_any_grant():
    """Combination behavior end-to-end: with the any-grant spent, a cheap task
    still dispatches while an expensive one stays pending."""
    store = MemoryStore()
    project = store.put(Project(
        name="p", spec_repo="s",
        agent_grants=[AgentGrant(sessions_per_day=1),
                      AgentGrant(backends=["codex"], models=["gpt-5.4-mini"])],
    ))
    seed_fleet(store, backends=("cursor", "codex"), runners=3)
    store.put(started_task("cursor").model_copy(update={"project_id": project.id}))
    cheap = pending_task(store, project, "https://example.com/a.git", "codex", "gpt-5.4-mini")
    expensive = pending_task(store, project, "https://example.com/b.git", "cursor")
    sup = Supervisor(store, orchestrate=lambda pid, events: None)
    assert sup.dispatch(project) == 1
    assert store.get(Task, cheap.id).status == TaskStatus.running
    assert store.get(Task, expensive.id).status == TaskStatus.pending


def test_probe_dispatches_despite_exhausted_grants():
    """Org health must not be hostage to a project's allowance."""
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="s",
                                agent_grants=[AgentGrant(sessions_per_day=0)]))
    seed_fleet(store, runners=1)
    pending_task(store, project, "probe-repo", kind=TaskKind.probe)
    sup = Supervisor(store, orchestrate=lambda pid, events: None)
    assert sup.dispatch(project) == 1


def test_grant_blocked_pending_rolls_up_to_blocked_budget():
    """Capacity exists, allowance is spent: the project reads blocked_budget
    (midnight reset), not blocked_resources (fleet problem) or fake working."""
    p = Project(name="p", spec_repo="x", agent_grants=[AgentGrant(sessions_per_day=1)])
    t = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i", backend="cursor")
    blocked = {t.id}
    assert compute_state(p, [], 0, [t], {"cursor"}, grant_blocked=blocked) == ProjectState.blocked_budget
    # Without capacity the honest answer is still blocked_resources.
    assert compute_state(p, [], 0, [t], set(), grant_blocked=blocked) == ProjectState.blocked_resources
    # And an unblocked twin keeps the project working.
    assert compute_state(p, [], 0, [t], {"cursor"}) == ProjectState.working


def test_refresh_state_reports_blocked_budget_when_allowance_spent():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="s",
                                agent_grants=[AgentGrant(sessions_per_day=1)]))
    seed_fleet(store)
    store.put(started_task("cursor").model_copy(update={"project_id": project.id}))
    pending_task(store, project, "https://example.com/a.git")
    sup = Supervisor(store, orchestrate=lambda pid, events: None)
    assert sup.refresh_state(project) == ProjectState.blocked_budget


# -- planner and pipeline steering --------------------------------------------


def planner_tools(store, project) -> Tools:
    return Tools(store, project, spec=None)


def test_create_task_rejects_disallowed_pair_and_names_the_allowance():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="s", agent_grants=[CHEAP]))
    ws = store.put(Workstream(project_id=project.id, title="w"))
    tools = planner_tools(store, project)
    out = tools.create_task(ws.id, "https://example.com/a.git", "do it", backend="cursor")
    assert out.startswith("error:") and "allowance" in out and "gpt-5.4-mini" in out
    ok = tools.create_task(ws.id, "https://example.com/a.git", "do it",
                           backend="codex", model="gpt-5.4-mini")
    assert ok.startswith("task_id=")
    task = store.list(Task, project_id=project.id)[-1]
    assert (task.backend, task.model) == ("codex", "gpt-5.4-mini")


def test_create_task_rejects_when_sessions_spent():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="s",
                                agent_grants=[AgentGrant(sessions_per_day=1)]))
    store.put(started_task("cursor").model_copy(update={"project_id": project.id}))
    ws = store.put(Workstream(project_id=project.id, title="w"))
    out = planner_tools(store, project).create_task(ws.id, "r", "do it", backend="cursor")
    assert out.startswith("error:") and "0/1 left today" in out


def test_snapshot_shows_the_allowance_line():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="s", agent_grants=[ANY_5, CHEAP]))
    snapshot = planner_tools(store, project).snapshot()
    assert "AGENT ALLOWANCE" in snapshot
    assert "codex × gpt-5.4-mini: unlimited" in snapshot
    # An unrestricted project advertises that too, so the planner never guesses.
    free = store.put(Project(name="f", spec_repo="s"))
    assert "AGENT ALLOWANCE" in planner_tools(store, free).snapshot()
    assert "no limits" in planner_tools(store, free).snapshot()


def test_issue_pipeline_remaps_onto_the_grant():
    """The configured default (codex) is remapped to the only permitted agent
    at the single task-creation choke point of the issue pipeline."""
    store = MemoryStore()
    grant = AgentGrant(backends=["gemini-cli"], models=["gemini-3-flash-preview"])
    project = store.put(Project(name="p", spec_repo="https://example.com/s.git",
                                agent_grants=[grant]))
    ws = store.put(ProjectWorkstream(project_id=project.id, repo=project.spec_repo,
                                     kind=ProjectWorkstreamKind.github_issues, title="issues"))
    reconcile(store, project,
              [{"number": 1, "title": "bug", "url": "u/1", "doc": "# bug\n\nb", "attachments": []}],
              workstream=ws)
    assert advance_issues(store, project, workstream=ws) == 1
    task = store.list(Task, project_id=project.id)[-1]
    assert task.kind == TaskKind.resolve
    assert (task.backend, task.model) == ("gemini-cli", "gemini-3-flash-preview")


def test_testing_episode_resolves_every_phase_through_grants():
    store = MemoryStore()
    grant = AgentGrant(backends=["gemini-cli"], models=["gemini-3-flash-preview"])
    project = store.put(Project(name="p", spec_repo="https://example.com/s.git",
                                agent_grants=[grant]))
    ws = store.put(ProjectWorkstream(project_id=project.id, repo=project.spec_repo,
                                     kind=ProjectWorkstreamKind.testing, title="testing"))
    episode, refresh = start_episode(store, project, ws)
    assert episode.refresh_backend == episode.sweep_backend == "gemini-cli"
    assert episode.sweep_model == "gemini-3-flash-preview"
    assert (refresh.backend, refresh.model) == ("gemini-cli", "gemini-3-flash-preview")
    solo = queue_refresh_task(store, project, ws)
    assert (solo.backend, solo.model) == ("gemini-cli", "gemini-3-flash-preview")


def test_intake_respects_grants():
    """A project whose allowance covers no trusted scout gets an actionable
    409 up front; one that covers claude gets the claude scout even when the
    default (codex) is installed and usable."""
    store = MemoryStore()
    runner = store.put(Runner(name="r", backends=["codex", "claude"]))
    for backend in ("codex", "claude"):
        store.put(Resource(runner_id=runner.id, backend=backend,
                           usability_status=ResourceUsability.usable))
    cheap_only = [AgentGrant(backends=["gemini-cli"])]
    with pytest.raises(HTTPException) as err:
        trusted_capacity(store, "default", grants=cheap_only)
    assert err.value.status_code == 409 and "allowance" in err.value.detail
    claude_ok = [AgentGrant(backends=["claude"])]
    backend, model, _ = trusted_capacity(store, "default", grants=claude_ok)
    assert (backend, model) in TRUSTED_SCOUTS and backend == "claude"
    # No grants: the historical default order still wins.
    backend, model, _ = trusted_capacity(store, "default")
    assert backend == "codex"
