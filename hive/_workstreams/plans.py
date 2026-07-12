"""Iteration plans: the doc-fed resolve → review → merge pipeline
(design: wiki/iteration-plan.md).

A `Plan` is the ordered list of `PlanItem`s for one iteration. The human
reviews a draft at whatever depth they choose (approve-all is the one-click
blind path), activation queues every item in one store transaction, and the
proven issue state machine executes them in strict order — fed by a work doc
synthesized from the item instead of a GitHub issue. No GitHub issues or PRs
are in the loop: blocked/rejected explanations land on the item itself
(`parked_reason`), and done = the item's branch merged on the remote default
branch (the same merges-API transport issue landing uses).

Strictness invariants:
- hive never executes iteration work that is not an approved plan item;
- one item in flight at a time, and a parked (blocked/rejected) item stalls
  the items behind it — later items may build on it, and parked means the
  human has a decision to make.
"""

from __future__ import annotations

import logging
import time

from hive._control.allowances import resolve_agent
from hive._control.escalation import escalate
from hive.models import (
    PLAN_ITEM_IN_FLIGHT,
    PLAN_ITEM_PARKED,
    PLAN_ITEM_TERMINAL,
    HumanTaskKind,
    Plan,
    PlanItem,
    PlanItemStatus,
    PlanStatus,
    Project,
    Task,
    TaskKind,
)
from hive.llm.prompts import load as load_prompt

log = logging.getLogger("hive._workstreams.plans")

PLAN_DOC_PATH = "iteration-plan.md"
LANDING_FAILED_PREFIX = "accepted but landing failed"
RESOLVE_BACKEND = "codex"  # same default agent as issue solving
REASON_LIMIT = 4000


def now_s() -> float:
    return time.time()


def plan_branch(item: PlanItem) -> str:
    return f"hive/plan-{item.id[:8]}"


# -- reading -------------------------------------------------------------------


def active_plan(store, project: Project) -> Plan | None:
    """The project's one live plan: draft or approved, newest wins."""
    plans = [
        p
        for p in store.list(Plan, workspace_id=project.workspace_id, project_id=project.id)
        if p.status in (PlanStatus.draft, PlanStatus.approved)
    ]
    return plans[-1] if plans else None


def plan_items(store, plan: Plan) -> list[PlanItem]:
    items = store.list(PlanItem, workspace_id=plan.workspace_id, plan_id=plan.id)
    return sorted(items, key=lambda i: (i.order, i.created_at))


def latest_plan(store, project: Project) -> Plan | None:
    """The plan a viewer should see: the active one, else the most recent
    finished one (so the rail persists after completion)."""
    live = active_plan(store, project)
    if live is not None:
        return live
    plans_all = store.list(Plan, workspace_id=project.workspace_id, project_id=project.id)
    return max(plans_all, key=lambda p: p.created_at) if plans_all else None


def cancel_plan_work(store, task: Task) -> None:
    """Release a plan item whose resolve/review task was hard-cancelled at the
    chief (never reached a runner). The runner-reported cancel path goes
    through TaskResultProcessor instead."""
    if task.kind not in (TaskKind.resolve, TaskKind.review) or not task.work_item_id:
        return
    item = store.get(PlanItem, task.work_item_id)
    if item is None or item.status not in PLAN_ITEM_IN_FLIGHT:
        return
    set_item_status(
        store,
        item.id,
        PlanItemStatus.blocked_clarity,
        "task cancelled by the operator — retry the item to continue",
    )


# -- drafting + review ----------------------------------------------------------


def create_draft(
    store, project: Project, goal: str, items: list[dict], proposed_by: str = "agent"
) -> Plan:
    """Create a draft plan, replacing (abandoning) any existing draft. An
    approved plan is never replaced silently — abandon it explicitly first."""
    existing = active_plan(store, project)
    if existing is not None:
        if existing.status == PlanStatus.approved:
            raise ValueError(
                "an approved plan is executing; abandon it before drafting a new one"
            )
        abandon_plan(store, existing)
    plan = store.put(
        Plan(
            workspace_id=project.workspace_id,
            project_id=project.id,
            goal=goal.strip(),
            proposed_by=proposed_by,
        )
    )
    for position, item in enumerate(items):
        _put_item(store, project, plan, item, order=position, authored_by=proposed_by)
    return plan


def _put_item(
    store, project: Project, plan: Plan, fields: dict, *, order: int, authored_by: str
) -> PlanItem:
    title = str(fields.get("title") or "").strip()
    if not title:
        raise ValueError("a plan item needs a title")
    return store.put(
        PlanItem(
            workspace_id=project.workspace_id,
            project_id=project.id,
            plan_id=plan.id,
            order=order,
            repo=str(fields.get("repo") or "").strip(),
            title=title,
            story=str(fields.get("story") or "").strip(),
            constraints=str(fields.get("constraints") or "").strip(),
            notes=str(fields.get("notes") or "").strip(),
            authored_by=authored_by,
        )
    )


def add_item(store, project: Project, plan: Plan, fields: dict, authored_by: str) -> PlanItem:
    """Append an item. On a draft this is normal assembly; on an approved plan
    it is an amendment proposal — the item enters `proposed` either way and
    needs the human's flip before it can execute."""
    if plan.status not in (PlanStatus.draft, PlanStatus.approved):
        raise ValueError(f"plan is {plan.status}; cannot add items")
    orders = [i.order for i in plan_items(store, plan)]
    return _put_item(
        store, project, plan, fields, order=(max(orders) + 1 if orders else 0), authored_by=authored_by
    )


EDITABLE_FIELDS = ("title", "story", "constraints", "notes", "repo", "order")


def update_item(store, item: PlanItem, fields: dict, by_human: bool = True) -> PlanItem:
    """Rewrite any part of an item. Allowed whenever no agent is working on it
    and it is not terminal — editing a parked item's constraints before a retry
    is the human's direct amendment path. Editing does not change approval."""
    if item.status in PLAN_ITEM_IN_FLIGHT:
        raise ValueError("an agent is working on this item; cancel its task first")
    if item.status in PLAN_ITEM_TERMINAL:
        raise ValueError(f"item is {item.status} and can no longer change")

    def mutate(saved: PlanItem) -> None:
        for key in EDITABLE_FIELDS:
            if key in fields and fields[key] is not None:
                setattr(saved, key, int(fields[key]) if key == "order" else str(fields[key]).strip())
        if by_human:
            saved.edited_by_human = True
        saved.updated_at = now_s()

    return store.update(PlanItem, item.id, mutate) or item


def approve_item(store, plan: Plan, item: PlanItem) -> PlanItem:
    """The human's flip. Before whole-plan approval it marks review progress;
    on an already-approved plan (an amendment) it queues the item directly."""
    if item.status != PlanItemStatus.proposed:
        raise ValueError(f"item is {item.status}; only proposed items can be approved")
    target = (
        PlanItemStatus.queued if plan.status == PlanStatus.approved else PlanItemStatus.approved
    )

    def mutate(saved: PlanItem) -> None:
        saved.status = target
        saved.updated_at = now_s()

    return store.update(PlanItem, item.id, mutate) or item


def unapprove_item(store, item: PlanItem) -> PlanItem:
    if item.status != PlanItemStatus.approved:
        raise ValueError(f"item is {item.status}; only approved (not yet queued) items can flip back")

    def mutate(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.proposed
        saved.updated_at = now_s()

    return store.update(PlanItem, item.id, mutate) or item


def approve_all(store, plan: Plan) -> int:
    """The one-click blind path: flip every proposed item. Equivalent to
    flipping each individually — same resulting states."""
    flipped = 0
    for item in plan_items(store, plan):
        if item.status == PlanItemStatus.proposed:
            approve_item(store, plan, item)
            flipped += 1
    return flipped


def cancel_item(store, item: PlanItem, reason: str = "") -> PlanItem:
    if item.status in PLAN_ITEM_IN_FLIGHT:
        raise ValueError("an agent is working on this item; cancel its task first")
    if item.status == PlanItemStatus.done:
        raise ValueError("item is done and can no longer change")

    def mutate(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.cancelled
        saved.parked_reason = reason.strip()
        saved.updated_at = now_s()

    return store.update(PlanItem, item.id, mutate) or item


def retry_item(store, project: Project, plan: Plan, item: PlanItem) -> PlanItem:
    """Re-queue a parked (blocked/rejected) item for another attempt — the
    human's move after editing constraints or fixing the blocker."""
    if item.status not in PLAN_ITEM_PARKED:
        raise ValueError(f"item is {item.status}; only blocked/rejected items can be retried")

    def mutate(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.queued
        saved.parked_reason = ""
        saved.updated_at = now_s()

    updated = store.update(PlanItem, item.id, mutate) or item
    advance_plan(store, project, store.get(Plan, plan.id) or plan)
    return updated


def abandon_plan(store, plan: Plan) -> Plan:
    """Terminal for the plan and every non-terminal item. In-flight agent tasks
    are cancelled by the caller (API) — the store flip here never strands one."""

    def mutate_plan(saved: Plan) -> None:
        saved.status = PlanStatus.abandoned
        saved.finished_at = now_s()

    for item in plan_items(store, plan):
        if item.status not in PLAN_ITEM_TERMINAL:
            def mutate_item(saved: PlanItem) -> None:
                saved.status = PlanItemStatus.cancelled
                saved.parked_reason = "plan abandoned"
                saved.updated_at = now_s()

            store.update(PlanItem, item.id, mutate_item)
    return store.update(Plan, plan.id, mutate_plan) or plan


# -- activation ------------------------------------------------------------------


def activation_problem(items: list[PlanItem]) -> str:
    """Why the plan cannot activate yet; empty when it can."""
    if not items:
        return "the plan has no items"
    pending = [i for i in items if i.status == PlanItemStatus.proposed]
    if pending:
        return f"{len(pending)} item(s) still awaiting approval"
    if not any(i.status == PlanItemStatus.approved for i in items):
        return "no approved items to queue"
    return ""


def activate(store, project: Project, plan: Plan, spec=None) -> list[str]:
    """Whole-plan approval: queue every approved item (store-only, atomic in
    effect — nothing external can half-fail), commit the plan document to the
    spec home as the durable record of what was approved, start execution.

    The store is the source of truth; the spec-home commit is best-effort and
    escalates a todo on failure rather than blocking the approved work."""
    if plan.status != PlanStatus.draft:
        raise ValueError(f"plan is {plan.status}; only a draft can be activated")
    items = plan_items(store, plan)
    if problem := activation_problem(items):
        raise ValueError(problem)

    notes: list[str] = []
    for item in items:
        if item.status != PlanItemStatus.approved:
            continue

        def queue(saved: PlanItem) -> None:
            saved.status = PlanItemStatus.queued
            saved.updated_at = now_s()

        store.update(PlanItem, item.id, queue)

    def approve(saved: Plan) -> None:
        saved.status = PlanStatus.approved
        saved.approved_at = now_s()

    plan = store.update(Plan, plan.id, approve) or plan
    notes.append(f"plan approved: {len(items)} item(s) queued")

    if spec is not None:
        try:
            spec.commit_files(
                {PLAN_DOC_PATH: plan_doc_markdown(plan, plan_items(store, plan))},
                f"Approved iteration plan: {plan.goal[:60]}",
            )
            store.update(Plan, plan.id, lambda saved: setattr(saved, "spec_ref", PLAN_DOC_PATH))
            notes.append(f"plan document committed to {PLAN_DOC_PATH}")
        except Exception as exc:
            log.warning("plan doc commit failed for %s: %s", plan.id, exc)
            escalate(
                store,
                f"Commit the approved plan doc for {project.name}",
                instructions=(
                    "The plan was approved and is executing, but committing "
                    f"`{PLAN_DOC_PATH}` to the spec home failed:\n\n```\n{exc}\n```\n\n"
                    "Usually push access. The store copy is authoritative; fix access "
                    "so the durable record lands."
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:plan-doc:{plan.id}",
            )
            notes.append("plan document commit failed (todo filed); execution continues")

    started = advance_plan(store, project, plan)
    if started:
        notes.append("first item started")
    return notes


def plan_doc_markdown(plan: Plan, items: list[PlanItem]) -> str:
    """The durable record committed to the spec home at approval."""
    lines = [
        "# Iteration plan",
        "",
        f"Goal: {plan.goal}",
        f"Proposed by: {plan.proposed_by} · approved {time.strftime('%Y-%m-%d', time.gmtime(now_s()))}",
        "",
    ]
    for position, item in enumerate(items, start=1):
        if item.status == PlanItemStatus.cancelled:
            continue
        lines += [f"## {position}. {item.title}", ""]
        if item.repo:
            lines += [f"Repo: {item.repo}", ""]
        if item.story:
            lines += ["**Story:** " + item.story, ""]
        if item.constraints:
            lines += ["**Constraints:** " + item.constraints, ""]
        if item.notes:
            lines += [item.notes, ""]
    return "\n".join(lines)


# -- execution: the doc-fed pipeline ----------------------------------------------


def build_work_doc(plan: Plan, item: PlanItem) -> str:
    """The item document the agent works from — every content field, verbatim."""
    parts = [f"# {item.title}", "", f"_Iteration goal: {plan.goal}_"]
    if item.story:
        parts += ["", "## Target user story", item.story]
    if item.constraints:
        parts += ["", "## Constraints", item.constraints]
    if item.notes:
        parts += ["", "## Notes", item.notes]
    return "\n".join(parts)


def _make_plan_task(
    store,
    project: Project,
    plan: Plan,
    item: PlanItem,
    kind: TaskKind,
    backend: str,
    model: str = "",
) -> Task:
    prompt_name = "plan_resolve" if kind == TaskKind.resolve else "plan_review"
    prompt, version = load_prompt(prompt_name)
    branch = plan_branch(item)
    position = item.order + 1
    header = (
        f"Iteration-plan item {position} ('{item.title}'), approved by the project owner.\n"
        f"You are on git branch `{branch}` (already checked out).\n\n"
        f"--- ITEM DOCUMENT ---\n{build_work_doc(plan, item)}\n--- END ITEM DOCUMENT ---\n"
    )
    backend, model = resolve_agent(project.agent_grants, backend, model)
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=item.id,
            work_item_id=item.id,
            run_id=plan.id,
            repo=item.repo or project.spec_repo,
            branch=branch,
            fresh_branch=kind == TaskKind.resolve,
            kind=kind,
            instructions=f"{header}\n{prompt}",
            backend=backend,
            model=model,
            prompt_versions={prompt_name: version},
        )
    )


def create_review_task(
    store, project: Project, plan: Plan, item: PlanItem, backend: str, model: str = ""
) -> Task:
    """Queue the independent fresh-agent review for a built item."""
    return _make_plan_task(store, project, plan, item, TaskKind.review, backend, model=model)


def advance_plan(
    store, project: Project, plan: Plan, backend: str = "", model: str = ""
) -> int:
    """Strict sequencing: if any item is in flight *or parked*, do nothing;
    otherwise promote the lowest-order queued item to `resolving` and queue its
    resolve task. Parked items stall the queue deliberately — later items may
    build on them, and a parked item is a decision waiting on the human.
    Idempotent; call after activation and after every plan-task landing."""
    if plan.status != PlanStatus.approved:
        return 0
    items = plan_items(store, plan)
    if any(i.status in PLAN_ITEM_IN_FLIGHT or i.status in PLAN_ITEM_PARKED for i in items):
        return 0
    queued = [i for i in items if i.status == PlanItemStatus.queued]
    if not queued:
        refresh_plan(store, plan)
        return 0
    nxt = queued[0]

    def promote(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.resolving
        saved.parked_reason = ""
        saved.updated_at = now_s()

    item = store.update(PlanItem, nxt.id, promote) or nxt
    _make_plan_task(store, project, plan, item, TaskKind.resolve, backend or RESOLVE_BACKEND, model=model)
    log.info(
        "plan %s: item %d '%s' → resolving (%d still queued)",
        plan.id, item.order + 1, item.title, len(queued) - 1,
    )
    return 1


def refresh_plan(store, plan: Plan) -> Plan:
    """Completion detection: an approved plan whose items are all terminal is
    complete (done items exist) — the moment the AI proposes the next
    iteration. All-cancelled degenerates to abandoned."""
    if plan.status != PlanStatus.approved:
        return plan
    items = plan_items(store, plan)
    if not items or any(i.status not in PLAN_ITEM_TERMINAL for i in items):
        return plan
    outcome = (
        PlanStatus.complete
        if any(i.status == PlanItemStatus.done for i in items)
        else PlanStatus.abandoned
    )

    def finish(saved: Plan) -> None:
        saved.status = outcome
        saved.finished_at = now_s()

    return store.update(Plan, plan.id, finish) or plan


def set_item_status(store, item_id: str, status: PlanItemStatus, reason: str) -> PlanItem | None:
    def mutate(saved: PlanItem) -> None:
        saved.status = status
        saved.parked_reason = reason.strip()[:REASON_LIMIT]
        saved.updated_at = now_s()

    return store.update(PlanItem, item_id, mutate)
