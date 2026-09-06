"""One complete, permission-aware collection of the workspace's open actions."""

from __future__ import annotations

import time
from typing import Literal

from hive._integrations.auth import AuthContext
from hive.models import (
    PLAN_ITEM_PARKED, HumanTask, HumanTaskStatus, Plan, PlanItem, PlanItemStatus,
    PlanStatus, Project, Question, QuestionStatus, User, WorkspaceMembership,
)
from hive.persistence import StoreBase

InboxKind = Literal["all", "question", "todo", "approval", "blocked"]
InboxScope = Literal["all", "yours", "others"]


def build_inbox(store: StoreBase, ctx: AuthContext, *, kind: InboxKind = "all",
                scope: InboxScope = "all", project_id: str = "", q: str = "",
                offset: int = 0, limit: int = 20) -> dict:
    """Filter after counting and paginate after sorting, so no action is hidden."""
    projects = {p.id: p for p in store.list(Project, workspace_id=ctx.workspace_id)}
    members = {m.user_id for m in store.list(WorkspaceMembership, workspace_id=ctx.workspace_id)}
    names = {user_id: user.github_login for user_id in members
             if (user := store.get(User, user_id)) is not None}
    rows = []

    def row(record, row_kind, title, body, **payload):
        project_id = record.project_id
        # Deleted projects cannot be acted on. Org-wide tasks deliberately have no project.
        if project_id and project_id not in projects:
            return
        assignee = record.assignee_user_id if isinstance(record, HumanTask) else ""
        can_act = ctx.is_admin or (bool(assignee) and assignee == ctx.user.id)
        waiting = assignee != ctx.user.id if assignee else not ctx.is_admin
        rows.append({
            "id": f"{row_kind}:{record.id}", "kind": row_kind,
            "title": title, "body": body, "project_id": project_id,
            "project_name": projects[project_id].name if project_id else "Workspace",
            "created_at": record.created_at, "can_act": can_act,
            "waiting_on_other": waiting, "assignee_user_id": assignee,
            "assignee_name": names.get(assignee, assignee) if assignee else "",
            **payload,
        })

    for question in store.list(Question, workspace_id=ctx.workspace_id, status=QuestionStatus.open):
        title = next((line.strip().lstrip("# ") for line in question.text.splitlines() if line.strip()), "Question")
        row(question, "question", title, question.text, question=question.model_dump())
    for todo in store.list(HumanTask, workspace_id=ctx.workspace_id, status=HumanTaskStatus.open):
        row(todo, "todo", todo.title, todo.instructions, todo=todo.model_dump())

    active_plans = {plan.id: plan for plan in store.list(Plan, workspace_id=ctx.workspace_id)
                    if plan.status in (PlanStatus.draft, PlanStatus.approved)}
    items_by_plan: dict[str, list[PlanItem]] = {}
    for item in store.list(PlanItem, workspace_id=ctx.workspace_id):
        if item.plan_id in active_plans and item.project_id == active_plans[item.plan_id].project_id:
            items_by_plan.setdefault(item.plan_id, []).append(item)
    for plan in active_plans.values():
        items = items_by_plan.get(plan.id, [])
        if plan.status == PlanStatus.draft:
            review_items = [item for item in items if item.status in (PlanItemStatus.proposed, PlanItemStatus.approved)]
            if review_items:
                row(plan, "approval", plan.goal,
                    f"Review {len(review_items)} work item{'s' if len(review_items) != 1 else ''} before starting this plan.",
                    plan=plan.model_dump())
        else:
            for item in items:
                if item.status == PlanItemStatus.proposed:
                    row(item, "approval", item.title, item.story or item.notes,
                        plan=plan.model_dump(), plan_item=item.model_dump())
                elif item.status in PLAN_ITEM_PARKED:
                    row(item, "blocked", item.title, item.parked_reason,
                        plan=plan.model_dump(), plan_item=item.model_dump())

    counts = {"all": len(rows), **{key: sum(row["kind"] == key for row in rows)
              for key in ("question", "todo", "approval", "blocked")},
              "yours": sum(not row["waiting_on_other"] for row in rows),
              "others": sum(row["waiting_on_other"] for row in rows)}
    query = q.strip().casefold()
    filtered = [row for row in rows
                if (kind == "all" or row["kind"] == kind)
                and (scope == "all" or row["waiting_on_other"] == (scope == "others"))
                and (not project_id or row["project_id"] == project_id)
                and (not query or query in " ".join((row["title"], row["body"], row["project_name"], row["assignee_name"])).casefold())]
    filtered.sort(key=lambda row: (row["waiting_on_other"], -row["created_at"], row["id"]))
    return {"items": filtered[offset:offset + limit], "total": len(filtered),
            "offset": offset, "limit": limit, "counts": counts, "observed_at": time.time()}
