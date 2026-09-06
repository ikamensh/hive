"""Read-only execution prerequisites, using the same agent policy as dispatch.

These are observations, not reservations or evidence that a repository was
cloned, validated or landed. In particular, a reviewer without an explicit role
inherits the builder's eventual choice; its preview is provisional.
"""

from __future__ import annotations

import time

from hive._control import allowances, pause
from hive._control.agent_choice import agent_candidates
from hive._control.supervisor import agent_problem, runner_problem, serializes_repo
from hive._workstreams import plans
from hive.models import (
    PLAN_ITEM_PARKED, PLAN_ITEM_TERMINAL, Plan, PlanStatus, Project,
    Resource, Runner, Task, TaskKind, TaskStatus,
)


def readiness(store, supervisor, config, project: Project, plan: Plan) -> dict:
    """Inspect settings and current build/review capacity without queueing work."""
    checks = []

    def check(key, label, status, detail, action=""):
        checks.append(dict(key=key, label=label, status=status, detail=detail, action=action))

    items = plans.plan_items(store, plan)
    remaining = [item for item in items if item.status not in PLAN_ITEM_TERMINAL]
    if plan.status not in (PlanStatus.draft, PlanStatus.approved):
        check("plan", "Plan", "blocked", f"This plan is {plan.status}. Inspect its results or submit a new plan.")
    elif not remaining:
        check("plan", "Work items", "blocked", "No remaining work items. Add an item before starting.", "items")
    elif parked := [item for item in remaining if item.status in PLAN_ITEM_PARKED]:
        check("plan", "Work items", "blocked", "; ".join(
            f"{item.title}: {item.parked_reason or item.status}" for item in parked
        ), "items")
    elif plan.status == PlanStatus.approved and remaining[0].status in ("proposed", "approved"):
        check("plan", "Work items", "blocked", f"Approve {remaining[0].title} to continue this plan.", "items")

    if project.archived:
        check("archived", "Project", "blocked", "Unarchive this project to dispatch work.", "settings")
    if project.paused:
        check("project_pause", "Project paused", "blocked", "Resume this project to dispatch queued work.", "resume_project")
    if pause.fleet_paused(store, project.workspace_id):
        check("fleet_pause", "Hive paused", "blocked", "Resume Hive to dispatch queued work across projects.", "resume_fleet")
    if supervisor.over_budget(project):
        check("budget", "Daily budget", "blocked", "Daily paid budget reached. Change the budget or wait until midnight UTC.", "settings")
    if not project.spec_repo:
        check("repository", "Repository", "blocked", "Connect a project repository before starting.", "settings")
    else:
        repos = sorted({item.repo or project.spec_repo for item in remaining})
        check("repository", "Target repositories", "info", "\n".join(repos) or project.spec_repo)
    if not config.gh_token:
        check("github", "GitHub access", "blocked", "Configure HIVE_GH_TOKEN on the chief so Hive can land reviewed work.")
    check("validation", "Validation for new reviews", "info", project.validation_command or "Agent review only; no validation command configured.")

    all_tasks = store.list(Task, workspace_id=project.workspace_id)
    project_tasks = [task for task in all_tasks if task.project_id == project.id]
    live = [task for task in project_tasks if task.run_id == plan.id
            and task.status in (TaskStatus.pending, TaskStatus.running)]
    left = allowances.remaining(project.agent_grants, allowances.sessions_today(
        project_tasks, allowances.utc_day_start(),
    ))
    runners = store.list(Runner, workspace_id=project.workspace_id)
    resources = {(r.runner_id, r.backend): r for r in store.list(Resource, workspace_id=project.workspace_id)}
    repo = (remaining[0].repo or project.spec_repo) if remaining else project.spec_repo

    def capacity(role, task):
        # A running attempt already selected its agent. New policy changes apply
        # to future dispatch, not retroactively to an active session.
        if task.status == TaskStatus.running:
            check(role, f"{role.title()} capacity", "ready", f"Running on {task.backend} ({task.runner_id}).")
            return task.backend, task.model
        candidates = agent_candidates(project, task)
        problems, waiting = [], []
        admitted = False
        for candidate in candidates:
            label = candidate.backend + (f" · {candidate.model}" if candidate.model else "")
            if problem := agent_problem(project, task, candidate, left):
                problems.append(f"{label}: {problem}")
                continue
            admitted = True
            for runner in runners:
                if problem := runner_problem(project, task, candidate, runner, resources.get((runner.id, candidate.backend))):
                    problems.append(f"{label} on {runner.name}: {problem}")
                    continue
                occupied = any(
                    other.status == TaskStatus.running
                    and other.id != task.id and other.run_id != plan.id
                    and (other.runner_id == runner.id or (serializes_repo(other) and other.repo == task.repo))
                    for other in all_tasks
                ) or any(
                    other.status == TaskStatus.pending and other.preserve_checkout
                    and other.id != task.id and other.resume_runner_id == runner.id and other.repo == task.repo
                    for other in all_tasks
                )
                if occupied:
                    waiting.append(f"{label} on {runner.name}: waiting for current work or a reserved checkout")
                    continue
                check(role, f"{role.title()} capacity", "ready", f"{label} on {runner.name}")
                return candidate.backend, candidate.model
            if not runners:
                problems.append(f"{label}: no registered runner")
        status = "waiting" if waiting else "blocked"
        detail = "; ".join(waiting or problems)
        check(role, f"{role.title()} capacity", status, detail, "resources" if admitted else "settings")
        return task.backend, task.model

    review = next((task for task in live if task.kind == TaskKind.review), None)
    if review is not None:
        check("build", "Build capacity", "info", "Build delivered; review is next.")
    else:
        backend, model = plans.plan_agent(store, project, TaskKind.resolve, plans.RESOLVE_BACKEND)
        build = next((task for task in live if task.kind == TaskKind.resolve), None)
        build = build or Task(project_id=project.id, workspace_id=project.workspace_id, repo=repo,
                              workstream_id="", instructions="", kind=TaskKind.resolve,
                              backend=backend, model=model)
        backend, model = capacity("build", build)
        backend, model = plans.plan_agent(store, project, TaskKind.review, backend, model)
        review = Task(project_id=project.id, workspace_id=project.workspace_id, repo=repo,
                      workstream_id="", instructions="", kind=TaskKind.review,
                      backend=backend, model=model)
    capacity("review", review)
    return {
        "plan_id": plan.id, "revision": plan.revision, "observed_at": time.time(),
        "ready": not any(check["status"] in ("blocked", "waiting") for check in checks),
        "checks": checks,
    }
