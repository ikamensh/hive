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
import threading
import time
from functools import wraps

from hive._control.agent_choice import build_agent
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
    TaskStatus,
    Verdict,
)
from hive.llm.prompts import load as load_prompt

log = logging.getLogger("hive._workstreams.plans")
lock = threading.RLock()  # the chief is one writer; serialize its API/planner/result threads


def synchronized(operation):
    @wraps(operation)
    def call(*args, **kwargs):
        with lock:
            return operation(*args, **kwargs)
    return call

PLAN_DOC_PATH = "iteration-plan.toml"
LANDING_FAILED_PREFIX = "accepted but landing failed"
RESOLVE_BACKEND = "codex"  # same default agent as issue solving
REASON_LIMIT = 4000
REPAIR_LIMIT = 2


def now_s() -> float:
    return time.time()


def _current(store, record):
    saved = store.get(type(record), record.id)
    if saved is None:
        raise ValueError(f"{type(record).__name__} {record.id} no longer exists")
    return saved


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


@synchronized
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


@synchronized
def create_draft(
    store, project: Project, goal: str, items: list[dict], proposed_by: str = "agent", *,
    source: str | None = None, submission_digest: str = "",
    context_documents: dict[str, str] | None = None, repo_baselines: dict[str, str] | None = None,
) -> Plan:
    """Create a draft plan, replacing (abandoning) any existing draft. An
    approved plan is never replaced silently — abandon it explicitly first."""
    from .plan_document import PlanDocument
    from .plan_artifacts import record_revision

    project = _current(store, project)
    parsed = PlanDocument.model_validate({
        "goal": goal, "items": items, "context_documents": context_documents or {},
        "repo_baselines": repo_baselines or {},
    })
    items = [item.model_dump() for item in parsed.items]
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
            goal=goal,
            proposed_by=proposed_by,
            context_documents=context_documents or {},
            repo_baselines=repo_baselines or {},
        )
    )
    for position, item in enumerate(items):
        _put_item(store, project, plan, item, order=position, authored_by=proposed_by)
    if project.pending_iteration_goal or project.goal_complete:
        requested_goal = project.pending_iteration_goal

        def consume_goal(saved: Project) -> None:
            if saved.pending_iteration_goal == requested_goal:
                saved.pending_iteration_goal = ""
            saved.goal_complete = False
            saved.goal_complete_note = ""

        store.update(Project, project.id, consume_goal)
        project.pending_iteration_goal = ""
        project.goal_complete = False
        project.goal_complete_note = ""
    return record_revision(store, plan, source=source, submission_digest=submission_digest,
                           authored_by=proposed_by, bump=False)


def _put_item(
    store, project: Project, plan: Plan, fields: dict, *, order: int, authored_by: str
) -> PlanItem:
    title = str(fields.get("title") or "")
    if not title.strip():
        raise ValueError("a plan item needs a title")
    return store.put(
        PlanItem(
            workspace_id=project.workspace_id,
            project_id=project.id,
            plan_id=plan.id,
            order=order,
            key=fields.get("key") or f"item-{order + 1}",
            repo=str(fields.get("repo") or project.spec_repo).strip(),
            title=title,
            story=str(fields.get("story") or ""),
            constraints=str(fields.get("constraints") or ""),
            notes=str(fields.get("notes") or ""),
            story_keys=fields.get("story_keys", []),
            authored_by=authored_by,
        )
    )


@synchronized
def add_item(store, project: Project, plan: Plan, fields: dict, authored_by: str) -> PlanItem:
    """Append an item. On a draft this is normal assembly; on an approved plan
    it is an amendment proposal — the item enters `proposed` either way and
    needs the human's flip before it can execute."""
    project, plan = _current(store, project), _current(store, plan)
    if plan.status not in (PlanStatus.draft, PlanStatus.approved):
        raise ValueError(f"plan is {plan.status}; cannot add items")
    existing = plan_items(store, plan)
    order = max((item.order for item in existing), default=-1) + 1
    from .plan_document import PlanDocument, PlanDocumentItem
    from .plan_artifacts import document, record_revision

    fields = PlanDocumentItem.model_validate(fields).model_dump(exclude_unset=True)
    if not fields.get("key"):
        keys = {item.key or item.id for item in existing}
        number = order + 1
        while f"item-{number}" in keys:
            number += 1
        fields["key"] = f"item-{number}"
    proposed = document(store, plan)
    proposed["items"].append(fields)
    PlanDocument.model_validate(proposed)
    item = _put_item(
        store, project, plan, fields, order=order, authored_by=authored_by
    )
    record_revision(store, plan, authored_by=authored_by)
    return item


EDITABLE_FIELDS = ("title", "story", "constraints", "notes", "repo", "order", "story_keys")


@synchronized
def update_item(store, item: PlanItem, fields: dict) -> PlanItem:
    """Rewrite any part of an item — the human's edit (the AI only adds via
    amendments). Allowed whenever no agent is working on it and it is not
    terminal: editing a parked item's constraints before a retry is the direct
    amendment path. Changed approved instructions need renewed approval."""
    item = _current(store, item)
    if item.status in PLAN_ITEM_IN_FLIGHT:
        raise ValueError("an agent is working on this item; cancel its task first")
    if item.status in PLAN_ITEM_TERMINAL:
        raise ValueError(f"item is {item.status} and can no longer change")

    from .plan_artifacts import document, record_revision
    from .plan_document import PlanDocument

    updates = {}
    for key in EDITABLE_FIELDS:
        if key in fields and fields[key] is not None:
            value = fields[key]
            updates[key] = int(value) if key == "order" else value if key == "story_keys" else str(value).strip()
    updates = {key: value for key, value in updates.items() if getattr(item, key) != value}
    if not updates:
        return item
    plan = store.get(Plan, item.plan_id)
    proposed = document(store, plan)
    for row in proposed["items"]:
        if row["key"] == (item.key or item.id):
            row.update({key: value for key, value in updates.items() if key != "order"})
    PlanDocument.model_validate(proposed)

    def mutate(saved: PlanItem) -> None:
        for key, value in updates.items():
            setattr(saved, key, value)
        if saved.status in (PlanItemStatus.approved, PlanItemStatus.queued):
            saved.status = PlanItemStatus.proposed
        saved.edited_by_human = True
        saved.updated_at = now_s()

    updated = store.update(PlanItem, item.id, mutate) or item
    record_revision(store, plan)
    return updated


@synchronized
def approve_item(store, plan: Plan, item: PlanItem) -> PlanItem:
    """The human's flip. Before whole-plan approval it marks review progress;
    on an already-approved plan (an amendment) it queues the item directly."""
    plan, item = _current(store, plan), _current(store, item)
    if item.status != PlanItemStatus.proposed:
        raise ValueError(f"item is {item.status}; only proposed items can be approved")
    target = (
        PlanItemStatus.queued if plan.status == PlanStatus.approved else PlanItemStatus.approved
    )

    def mutate(saved: PlanItem) -> None:
        saved.status = target
        saved.updated_at = now_s()

    return store.update(PlanItem, item.id, mutate) or item


@synchronized
def unapprove_item(store, item: PlanItem) -> PlanItem:
    item = _current(store, item)
    if item.status != PlanItemStatus.approved:
        raise ValueError(f"item is {item.status}; only approved (not yet queued) items can flip back")

    def mutate(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.proposed
        saved.updated_at = now_s()

    return store.update(PlanItem, item.id, mutate) or item


@synchronized
def approve_all(store, plan: Plan) -> int:
    """The one-click blind path: flip every proposed item. Equivalent to
    flipping each individually — same resulting states."""
    plan = _current(store, plan)
    flipped = 0
    for item in plan_items(store, plan):
        if item.status == PlanItemStatus.proposed:
            approve_item(store, plan, item)
            flipped += 1
    return flipped


@synchronized
def cancel_item(store, item: PlanItem, reason: str = "") -> PlanItem:
    item = _current(store, item)
    if item.status in PLAN_ITEM_IN_FLIGHT:
        raise ValueError("an agent is working on this item; cancel its task first")
    if item.status == PlanItemStatus.done:
        raise ValueError("item is done and can no longer change")

    def mutate(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.cancelled
        saved.parked_reason = reason.strip()
        saved.updated_at = now_s()

    return store.update(PlanItem, item.id, mutate) or item


@synchronized
def retry_item(store, project: Project, plan: Plan, item: PlanItem) -> PlanItem:
    """Re-queue a parked item, retaining its checkout when execution resumes."""
    project, plan, item = _current(store, project), _current(store, plan), _current(store, item)
    if item.status not in PLAN_ITEM_PARKED:
        raise ValueError(f"item is {item.status}; only blocked/rejected items can be retried")

    def mutate(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.queued
        saved.parked_reason = ""
        saved.repair_attempts = 0
        saved.updated_at = now_s()

    updated = store.update(PlanItem, item.id, mutate) or item
    advance_plan(store, project, store.get(Plan, plan.id) or plan)
    return updated


@synchronized
def abandon_plan(store, plan: Plan) -> Plan:
    """Terminal for the plan and every non-terminal item. In-flight agent tasks
    are cancelled by the caller (API) — the store flip here never strands one."""

    plan = _current(store, plan)

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


@synchronized
def activate(store, project: Project, plan: Plan, spec=None) -> list[str]:
    """Whole-plan approval: queue every approved item (store-only, atomic in
    effect — nothing external can half-fail), commit the plan document to the
    spec home as the durable record of what was approved, start execution.

    The store is the source of truth; the spec-home commit is best-effort and
    escalates a todo on failure rather than blocking the approved work."""
    project, plan = _current(store, project), _current(store, plan)
    if plan.status != PlanStatus.draft:
        raise ValueError(f"plan is {plan.status}; only a draft can be activated")
    items = plan_items(store, plan)
    if problem := activation_problem(items):
        raise ValueError(problem)

    notes: list[str] = []
    queued_count = 0
    for item in items:
        if item.status != PlanItemStatus.approved:
            continue

        def queue(saved: PlanItem) -> None:
            saved.status = PlanItemStatus.queued
            saved.updated_at = now_s()

        store.update(PlanItem, item.id, queue)
        queued_count += 1

    def approve(saved: Plan) -> None:
        saved.status = PlanStatus.approved
        saved.approved_at = now_s()

    plan = store.update(Plan, plan.id, approve) or plan
    notes.append(f"plan approved: {queued_count} item(s) queued")

    if spec is not None:
        try:
            from .plan_artifacts import document
            from .plan_document import render_plan_document

            spec.commit_files(
                {PLAN_DOC_PATH: render_plan_document(document(store, plan))},
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


def plan_agent(store, project: Project, kind: TaskKind, backend: str, model: str = "") -> tuple[str, str]:
    """Select the initial role agent for both queueing and the readiness preview."""
    role = "build" if kind == TaskKind.resolve else "review"
    selected = getattr(project, f"{role}_backend")
    if selected:
        backend, model = selected, getattr(project, f"{role}_model")
    elif project.agent_preferences:
        backend, model = project.agent_preferences[0].backend, project.agent_preferences[0].model
    else:
        backend, model = build_agent(store, project, backend, model)
    if backend == "opencode" and not model:
        from hive.agents.backends import opencode_model
        model = opencode_model()
    return backend, model


@synchronized
def _make_plan_task(
    store,
    project: Project,
    plan: Plan,
    item: PlanItem,
    kind: TaskKind,
    backend: str,
    model: str = "",
    *,
    continue_from: Task | None = None,
    feedback: str = "",
) -> Task:
    project, plan, item = _current(store, project), _current(store, plan), _current(store, item)
    prompt_name = "plan_resolve" if kind == TaskKind.resolve else "plan_review"
    prompt, version = load_prompt(prompt_name)
    branch = plan_branch(item)
    position = item.order + 1
    header = (
        f"Iteration-plan item {position} ('{item.title}'), approved by the project owner.\n"
        f"You are on git branch `{branch}` (already checked out).\n\n"
        f"--- ITEM DOCUMENT ---\n{build_work_doc(plan, item)}\n--- END ITEM DOCUMENT ---\n"
    )
    for name, content in plan.context_documents.items():
        header += f"\n--- CONTEXT DOCUMENT: {name} ---\n{content}\n--- END CONTEXT ---\n"
    if plan.repo_baselines:
        header += "\nPlanning baselines (earlier plan items may have advanced these repos):\n"
        header += "\n".join(f"{repo}: {sha}" for repo, sha in plan.repo_baselines.items()) + "\n"
    if project.validation_command:
        header += f"\nRequired validation command: `{project.validation_command}`.\n"
    if continue_from is not None:
        header += ("\nContinue the existing work on this branch, including uncommitted edits. "
                   "Use the current item document above and the previous attempt's report below. "
                   "Complete, validate, commit and push the result.\n\n"
                   + (feedback or continue_from.result_text)[-REASON_LIMIT:] + "\n")
    backend, model = plan_agent(store, project, kind, backend, model)
    return store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id=item.id,
            work_item_id=item.id,
            run_id=plan.id,
            plan_revision=plan.revision,
            repo=item.repo or project.spec_repo,
            branch=branch,
            fresh_branch=kind == TaskKind.resolve and continue_from is None,
            preserve_checkout=continue_from is not None,
            resume_runner_id=(continue_from.runner_id or continue_from.resume_runner_id)
            if continue_from else "",
            retry_of_task_id=continue_from.id if continue_from else "",
            kind=kind,
            validation_command=project.validation_command if kind == TaskKind.review else "",
            instructions=f"{header}\n{prompt}",
            backend=backend,
            model=model,
            prompt_versions={prompt_name: version},
        )
    )


@synchronized
def create_review_task(
    store, project: Project, plan: Plan, item: PlanItem, backend: str, model: str = ""
) -> Task:
    """Queue the independent fresh-agent review for a built item."""
    return _make_plan_task(store, project, plan, item, TaskKind.review, backend, model=model)


@synchronized
def repair_after_review(
    store, project: Project, plan: Plan, item: PlanItem, task: Task, report: str,
) -> None:
    """Keep the reviewed checkout for two repair attempts, then park with evidence."""
    project, plan, item = _current(store, project), _current(store, plan), _current(store, item)
    if item.repair_attempts >= REPAIR_LIMIT:
        set_item_status(store, item.id, PlanItemStatus.rejected,
                        f"Still failing after {REPAIR_LIMIT} repair attempts:\n\n{report}")
        return

    def repair(saved: PlanItem) -> None:
        saved.status = PlanItemStatus.resolving
        saved.repair_attempts += 1
        saved.parked_reason = ""
        saved.updated_at = now_s()

    item = store.update(PlanItem, item.id, repair) or item
    _make_plan_task(store, project, plan, item, TaskKind.resolve, task.backend,
                    continue_from=task, feedback=report)


@synchronized
def advance_plan(
    store, project: Project, plan: Plan, backend: str = "", model: str = ""
) -> int:
    """Strict sequencing: if any item is in flight *or parked*, do nothing;
    otherwise start the lowest-order queued item, resuming an unfinished review
    when its delivered attempt failed without a product/gate rejection.
    Parked items stall the queue deliberately — later items may
    build on them, and a parked item is a decision waiting on the human.
    Idempotent; call after activation and after every plan-task landing."""
    project, plan = _current(store, project), _current(store, plan)
    from .plan_artifacts import recover_submissions
    recover_submissions(store, project, plan_id=plan.id)
    plan = _current(store, plan)
    if plan.status != PlanStatus.approved:
        return 0
    items = plan_items(store, plan)
    if any(i.status in PLAN_ITEM_IN_FLIGHT or i.status in PLAN_ITEM_PARKED for i in items):
        return 0
    remaining = [i for i in items if i.status not in PLAN_ITEM_TERMINAL]
    if remaining and remaining[0].status in (PlanItemStatus.proposed, PlanItemStatus.approved):
        return 0  # an unapproved amendment cannot be skipped by later queued work
    queued = [i for i in items if i.status == PlanItemStatus.queued]
    if not queued:
        refresh_plan(store, plan)
        return 0
    nxt = queued[0]

    # A manual retry may wait behind another item. Derive its continuation from
    # durable delivered-attempt history here, so a chief restart cannot lose the
    # checkout. An undelivered attempt never owned one, even if it was dispatched.
    previous = max(
        (task for task in store.list(Task, workspace_id=project.workspace_id, work_item_id=nxt.id)
         if task.run_id == plan.id and task.repo == (nxt.repo or project.spec_repo)
         and task.branch == plan_branch(nxt)
         and task.delivered
         and task.status in (TaskStatus.done, TaskStatus.failed, TaskStatus.cancelled)),
        key=lambda task: task.created_at, default=None,
    )
    retry_review = (
        previous is not None and previous.kind == TaskKind.review
        and previous.status == TaskStatus.failed and previous.verdict == Verdict.none
        and (previous.validation is None or previous.validation.exit_code == 0)
    )
    kind = TaskKind.review if retry_review else TaskKind.resolve
    status = PlanItemStatus.reviewing if retry_review else PlanItemStatus.resolving

    def promote(saved: PlanItem) -> None:
        saved.status = status
        saved.parked_reason = ""
        saved.updated_at = now_s()

    item = store.update(PlanItem, nxt.id, promote) or nxt
    _make_plan_task(store, project, plan, item, kind, backend or RESOLVE_BACKEND,
                    model=model, continue_from=previous)
    log.info(
        "plan %s: item %d '%s' → %s (%d still queued)",
        plan.id, item.order + 1, item.title, status, len(queued) - 1,
    )
    return 1


@synchronized
def refresh_plan(store, plan: Plan) -> Plan:
    """Completion detection: an approved plan whose items are all terminal is
    complete (done items exist) — the moment the AI proposes the next
    iteration. All-cancelled degenerates to abandoned."""
    plan = _current(store, plan)
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


@synchronized
def set_item_status(store, item_id: str, status: PlanItemStatus, reason: str) -> PlanItem | None:
    def mutate(saved: PlanItem) -> None:
        saved.status = status
        saved.parked_reason = reason.strip()[:REASON_LIMIT]
        saved.updated_at = now_s()

    return store.update(PlanItem, item_id, mutate)
