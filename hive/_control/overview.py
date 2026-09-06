"""Single-pass home-dashboard overview.

The web home page used to assemble its state by fetching the project list and
then one heavy detail payload per project (an N+1 waterfall). This builds the
same picture server-side in one read so the client makes a single request and
the counting lives in one place.

Read-only: it never creates default workstreams or mutates records, so it is
safe to poll frequently.
"""

from __future__ import annotations

from collections import defaultdict
import time
from typing import Callable

from hive._control import pause
from hive._control.capacity import capacity_summary, group_machines
from hive._control.supervisor import state_reason
from hive._workstreams.plan_results import execution_summary
from hive._workstreams.testing import automatic_testing_enabled, story_health
from hive.models import (
    HumanTask,
    HumanTaskStatus,
    Machine,
    Plan,
    PlanItem,
    PlanItemStatus,
    PlanStatus,
    Project,
    ProjectState,
    ProjectWorkstream,
    ProjectWorkstreamKind,
    Question,
    QuestionStatus,
    Resource,
    Runner,
    Story,
    Subscription,
    Task,
    TaskKind,
    TaskStatus,
    ISSUE_BLOCKED,
    IssueItem,
)

# Issue-workstream states that need the human before Hive can proceed.

# Live-task and attention lists are capped — the dashboard shows highlights and
# links into the project for the full set.
LIVE_TASK_CAP = 30
ATTENTION_CAP = 20
DELIVERY_CAP = 8

TASK_TITLES = {
    TaskKind.work: "Implementation work",
    TaskKind.verify: "Verify changes",
    TaskKind.probe: "Check agent access",
    TaskKind.intake: "Prepare the project specification",
    TaskKind.resolve: "Implement requested changes",
    TaskKind.review: "Review changes",
    TaskKind.preflight: "Check repository access",
    TaskKind.test_refresh: "Refresh acceptance stories",
    TaskKind.test_sweep: "Test acceptance stories",
    TaskKind.test_reproduce: "Reproduce a finding",
    TaskKind.test_judge: "Review a testing finding",
    TaskKind.testability_draft: "Prepare the testing environment",
    TaskKind.testability_probe: "Check the testing environment",
}


def _bucket(items, key) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for item in items:
        out[key(item)].append(item)
    return out


def _recent_deliveries(projects: list[Project], items: list[PlanItem], tasks: list[Task]) -> dict:
    """Recorded landings only; checks for another commit remain distinct evidence."""
    projects_by_id = {project.id: project for project in projects}
    landed = [item for item in items if item.project_id in projects_by_id
              and item.status == PlanItemStatus.done and item.merged_commit_sha and item.merged_at > 0]
    landed.sort(key=lambda item: (item.merged_at, item.id), reverse=True)
    checked = _bucket(
        (task for task in tasks if task.status == TaskStatus.done and task.validation is not None),
        lambda task: (task.project_id, task.run_id, task.work_item_id),
    )
    rows = []
    for item in landed[:DELIVERY_CAP]:
        project = projects_by_id[item.project_id]
        attempt = max(checked.get((item.project_id, item.plan_id, item.id), []),
                      key=lambda task: (task.finished_at, task.created_at, task.id), default=None)
        validation = {"status": "not_recorded", "command": "", "task_id": "", "commit_sha": ""}
        if attempt is not None:
            check = attempt.validation
            status = "not_recorded"
            if check.commit_sha:
                status = ("different_commit" if check.commit_sha != item.merged_commit_sha
                          else "passed" if check.exit_code == 0 and not attempt.is_error else "failed")
            validation = {"status": status, "command": check.command,
                          "task_id": attempt.id, "commit_sha": check.commit_sha}
        rows.append({
            "project_id": project.id,
            "project_name": project.name,
            "plan_id": item.plan_id,
            "work_item_id": item.id,
            "title": item.title,
            "repo": item.repo or project.spec_repo,
            "merged_commit_sha": item.merged_commit_sha,
            "merged_at": item.merged_at,
            "validation": validation,
        })
    return {"count": len(landed), "items": rows}


def testing_offers(
    projects: list[Project],
    streams_by_project: dict[str, list[ProjectWorkstream]],
    stories_by_workstream: dict[str, list[Story]],
    refreshing_workstreams: set[str],
) -> list[dict]:
    """Standing testing offers hive cannot act on by itself.

    A project with automatic testing and included capacity or a daily budget is
    already handled by the supervisor's testing tick, a paused project said
    stop, and an intake-stage project has no approved spec to test against —
    none belong on the dashboard. What remains is the honest ask: 'Hive can do
    X here, let it' — surfaced with the health verdict so one click/command
    accepts it.
    """
    offers = []
    for project in projects:
        if project.paused or project.state == ProjectState.intake:
            continue
        if automatic_testing_enabled(project):
            continue
        for workstream in streams_by_project.get(project.id, []):
            if workstream.kind != ProjectWorkstreamKind.testing or not workstream.enabled:
                continue
            health = story_health(
                stories_by_workstream.get(workstream.id, []),
                refresh_active=workstream.id in refreshing_workstreams,
            )
            if not health.action:
                continue
            offers.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "workstream_id": workstream.id,
                    "repo": workstream.repo,
                    "state": health.state,
                    "summary": health.summary,
                    "offer": health.offer,
                    "action": health.action,
                }
            )
    return offers


def build_overview(store, workspace_id: str, spend_today: Callable[[str], float]) -> dict:
    """Assemble everything the home dashboard needs in one pass.

    `spend_today(project_id)` is injected (the supervisor owns that sum) so this
    stays a pure read over the store and is trivial to test.
    """
    observed_at = time.time()
    projects = [
        p for p in store.list(Project, workspace_id=workspace_id) if not p.archived
    ]
    name_by_id = {p.id: p.name for p in projects}

    all_tasks = store.list(Task, workspace_id=workspace_id)
    runners = store.list(Runner, workspace_id=workspace_id)
    resources = store.list(Resource, workspace_id=workspace_id)
    resource_by_id = {resource.id: resource for resource in resources}
    online_runner_ids = {r.id for r in runners if r.online()}
    available_backends = {
        res.backend
        for res in resources
        if res.available() and res.runner_id in online_runner_ids
    }
    issue_items = store.list(IssueItem, workspace_id=workspace_id)
    streams = store.list(ProjectWorkstream, workspace_id=workspace_id)
    stories = store.list(Story, workspace_id=workspace_id)
    items_by_project = _bucket(issue_items, lambda w: w.project_id)
    streams_by_project = _bucket(streams, lambda s: s.project_id)
    tasks_by_project = _bucket(all_tasks, lambda t: t.project_id)
    tasks_by_plan = _bucket(all_tasks, lambda t: t.run_id)
    plans_by_project = _bucket(store.list(Plan, workspace_id=workspace_id), lambda p: p.project_id)
    plan_items = store.list(PlanItem, workspace_id=workspace_id)
    items_by_plan = _bucket(plan_items, lambda i: i.plan_id)
    work_by_id = {item.id: item for item in [*issue_items, *plan_items, *stories, *streams]}
    runner_by_id = {runner.id: runner for runner in runners}
    questions = store.list(Question, workspace_id=workspace_id)
    q_by_project = _bucket(questions, lambda q: q.project_id)

    project_rows = []
    spend_total = 0.0
    budget_total = 0.0
    for project in projects:
        items = items_by_project.get(project.id, [])
        tasks = tasks_by_project.get(project.id, [])
        plans = plans_by_project.get(project.id, [])
        active = [p for p in plans if p.status in (PlanStatus.draft, PlanStatus.approved)]
        plan = active[-1] if active else max(plans, key=lambda p: p.created_at, default=None)
        plan_summary = None
        if plan is not None:
            summary = execution_summary(plan, items_by_plan.get(plan.id, []), tasks_by_plan.get(plan.id, []))
            plan_summary = {
                "id": plan.id, "goal": plan.goal, "status": plan.status,
                "outcome": summary.outcome, "counts": summary.counts.model_dump(),
            }
        spend = spend_today(project.id)
        spend_total += spend
        if project.daily_budget_usd > 0:
            budget_total += project.daily_budget_usd
        project_rows.append(
            {
                "id": project.id,
                "name": project.name,
                "spec_repo": project.spec_repo,
                "state": project.state,
                "state_reason": state_reason(store, project, available_backends, spend),
                "paused": project.paused,
                "created_at": project.created_at,
                "daily_budget_usd": project.daily_budget_usd,
                "spend_today": spend,
                "plan": plan_summary,
                "counts": {
                    "running": sum(1 for t in tasks if t.status == TaskStatus.running),
                    "questions": sum(
                        1 for q in q_by_project.get(project.id, []) if q.status == QuestionStatus.open
                    ),
                    "blockers": sum(1 for w in items if w.status in ISSUE_BLOCKED),
                },
            }
        )

    machine_groups = group_machines(
        store.list(Machine, workspace_id=workspace_id),
        runners,
        resources,
    )
    capacity = capacity_summary(machine_groups)
    machine_by_runner = {runner.id: group.machine for group in machine_groups for runner in group.runners}

    running = [t for t in all_tasks if t.status == TaskStatus.running]
    running.sort(key=lambda t: t.started_at, reverse=True)
    busy_runner_ids = {task.runner_id for task in running if task.runner_id}
    for group, card in zip(machine_groups, capacity["machines"]):
        for agent in card["agents"]:
            agent.update(resource_by_id[agent["id"]].model_dump(include={
                "usage_plan", "usage_source", "usage_captured_at", "usage_windows", "model_cooldowns",
            }))
        runner_ids = {runner.id for runner in group.runners}
        online = runner_ids & online_runner_ids
        usable = {agent["runner_id"] for agent in card["agents"] if agent["available"]}
        card.update({
            "runners_total": len(runner_ids),
            "runners_online": len(online),
            "runners_busy": len(online & busy_runner_ids),
            "runners_usable": len(usable),
            "runners_idle": len(usable - busy_runner_ids),
        })
    for key in ("runners_total", "runners_online", "runners_busy", "runners_usable", "runners_idle"):
        capacity[key] = sum(card[key] for card in capacity["machines"])

    live_tasks = []
    for t in running[:LIVE_TASK_CAP]:
        work = work_by_id.get(t.work_item_id or t.workstream_id)
        runner = runner_by_id.get(t.runner_id)
        machine = machine_by_runner.get(t.runner_id)
        live_tasks.append({
            "id": t.id,
            "project_id": t.project_id,
            "project_name": name_by_id.get(t.project_id, ""),
            "backend": t.backend,
            "model": t.model,
            "kind": t.kind,
            "started_at": t.started_at,
            "issue_number": t.issue_number,
            "title": work.title if work and work.title else TASK_TITLES[t.kind],
            "runner_id": t.runner_id,
            "runner_name": runner.name if runner else "",
            "machine_id": machine.id if machine else "",
            "machine_name": machine.name if machine else "",
            "runner_online": t.runner_id in online_runner_ids,
            "runner_last_seen": runner.last_seen if runner else 0.0,
            "delivered": t.delivered,
            "plan_id": work.plan_id if isinstance(work, PlanItem) else "",
            "work_item_id": work.id if work else t.work_item_id,
            "repo": t.repo,
        })

    offers = testing_offers(
        projects,
        streams_by_project,
        _bucket(stories, lambda s: s.workstream_id),
        {
            t.workstream_id
            for t in all_tasks
            if t.kind == TaskKind.test_refresh
            and t.status in (TaskStatus.pending, TaskStatus.running)
        },
    )

    open_questions = [q for q in questions if q.status == QuestionStatus.open]
    open_questions.sort(key=lambda q: q.created_at, reverse=True)
    open_todos = [
        t
        for t in store.list(HumanTask, workspace_id=workspace_id)
        if t.status == HumanTaskStatus.open
    ]
    open_todos.sort(key=lambda t: t.created_at, reverse=True)
    attention = {
        # Offers are hive asking for permission, not the human being blocked on
        # — they ride the attention payload but stay out of the needs-you count.
        "count": len(open_questions) + len(open_todos),
        "offers": offers[:ATTENTION_CAP],
        "questions": [
            {
                "id": q.id,
                "project_id": q.project_id,
                "project_name": name_by_id.get(q.project_id, ""),
                "text": q.text,
                "created_at": q.created_at,
            }
            for q in open_questions[:ATTENTION_CAP]
        ],
        "human_todos": [
            {
                "id": t.id,
                "project_id": t.project_id,
                "project_name": name_by_id.get(t.project_id, "") if t.project_id else "",
                "assignee_user_id": t.assignee_user_id,
                "title": t.title,
                "instructions": t.instructions,
                "created_at": t.created_at,
            }
            for t in open_todos[:ATTENTION_CAP]
        ],
    }

    subscriptions = [
        s.model_dump() for s in store.list(Subscription, workspace_id=workspace_id)
    ]

    return {
        "observed_at": observed_at,
        "tasks_pending": sum(t.status == TaskStatus.pending for t in all_tasks),
        "paused": pause.fleet_paused(store, workspace_id),
        "projects": project_rows,
        "capacity": capacity,
        "live_tasks": live_tasks,
        "recent_deliveries": _recent_deliveries(projects, plan_items, all_tasks),
        "attention": attention,
        "subscriptions": subscriptions,
        "totals": {
            "tasks_running": len(running),
            "agents_ready": capacity["agents_ready"],
            "agents_total": capacity["agents_total"],
            "machines_online": capacity["machines_online"],
            "machines_total": capacity["machines_total"],
            "needs_you": attention["count"],
            "spend_today": spend_total,
            "budget_today": budget_total,
        },
    }
