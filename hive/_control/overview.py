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
from typing import Callable

from hive._control.capacity import capacity_summary, group_machines
from hive._workstreams.testing import story_health
from hive.models import (
    HumanTask,
    HumanTaskStatus,
    Machine,
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
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)

# Issue-workstream states that need the human before Hive can proceed.
ISSUE_BLOCKED = (WorkstreamStatus.blocked_clarity, WorkstreamStatus.rejected)

# Live-task and attention lists are capped — the dashboard shows highlights and
# links into the project for the full set.
LIVE_TASK_CAP = 30
ATTENTION_CAP = 20


def _bucket(items, key) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for item in items:
        out[key(item)].append(item)
    return out


def testing_offers(
    projects: list[Project],
    streams_by_project: dict[str, list[ProjectWorkstream]],
    stories_by_workstream: dict[str, list[Story]],
    refreshing_workstreams: set[str],
) -> list[dict]:
    """Standing testing offers hive cannot act on by itself.

    A project inside the autonomy envelope (testing_auto + a daily budget) is
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
        if project.testing_auto and project.daily_budget_usd > 0:
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
    projects = [
        p for p in store.list(Project, workspace_id=workspace_id) if not p.archived
    ]
    name_by_id = {p.id: p.name for p in projects}

    all_tasks = store.list(Task, workspace_id=workspace_id)
    items_by_project = _bucket(store.list(Workstream, workspace_id=workspace_id), lambda w: w.project_id)
    streams_by_project = _bucket(store.list(ProjectWorkstream, workspace_id=workspace_id), lambda s: s.project_id)
    tasks_by_project = _bucket(all_tasks, lambda t: t.project_id)
    questions = store.list(Question, workspace_id=workspace_id)
    q_by_project = _bucket(questions, lambda q: q.project_id)

    project_rows = []
    spend_total = 0.0
    budget_total = 0.0
    for project in projects:
        items = items_by_project.get(project.id, [])
        tasks = tasks_by_project.get(project.id, [])
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
                "paused": project.paused,
                "created_at": project.created_at,
                "daily_budget_usd": project.daily_budget_usd,
                "spend_today": spend,
                "counts": {
                    "active": sum(1 for w in items if w.status == WorkstreamStatus.active),
                    "running": sum(1 for t in tasks if t.status == TaskStatus.running),
                    "questions": sum(
                        1 for q in q_by_project.get(project.id, []) if q.status == QuestionStatus.open
                    ),
                    "blockers": sum(
                        1
                        for w in items
                        if w.source == WorkstreamSource.issue and w.status in ISSUE_BLOCKED
                    ),
                    "streams": len(streams_by_project.get(project.id, [])),
                },
            }
        )

    capacity = capacity_summary(
        group_machines(
            store.list(Machine, workspace_id=workspace_id),
            store.list(Runner, workspace_id=workspace_id),
            store.list(Resource, workspace_id=workspace_id),
        )
    )

    running = [t for t in all_tasks if t.status == TaskStatus.running]
    running.sort(key=lambda t: t.started_at, reverse=True)
    live_tasks = [
        {
            "id": t.id,
            "project_id": t.project_id,
            "project_name": name_by_id.get(t.project_id, ""),
            "backend": t.backend,
            "model": t.model,
            "kind": t.kind,
            "started_at": t.started_at,
            "issue_number": t.issue_number,
        }
        for t in running[:LIVE_TASK_CAP]
    ]

    offers = testing_offers(
        projects,
        streams_by_project,
        _bucket(store.list(Story, workspace_id=workspace_id), lambda s: s.workstream_id),
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
        "projects": project_rows,
        "capacity": capacity,
        "live_tasks": live_tasks,
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
