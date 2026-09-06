"""Portable plan inputs and retained revisions, shared by every plan author."""

from __future__ import annotations

import hashlib

from hive.models import (
    PLAN_ITEM_IN_FLIGHT, PLAN_ITEM_TERMINAL, Plan, PlanItem, PlanItemStatus, PlanRevision,
    PlanStatus, PlanSubmission, Project, new_id,
)
from .plan_document import PlanDocument, parse_plan_document, render_plan_document


class RevisionConflict(ValueError):
    """An author must reconcile their draft with the accepted revision first."""


ITEM_FIELDS = ("key", "title", "story", "constraints", "notes", "repo", "story_keys")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def document(store, plan: Plan) -> dict:
    from .plans import plan_items

    return PlanDocument.model_validate({
        "version": 1, "plan_id": plan.id, "revision": plan.revision, "goal": plan.goal,
        "context_documents": plan.context_documents, "repo_baselines": plan.repo_baselines,
        "items": [{field: getattr(item, field) for field in ITEM_FIELDS}
                  | {"key": item.key or item.id} for item in plan_items(store, plan)],
    }).model_dump()


def record_revision(store, plan: Plan, *, source: str | None = None,
                    submission_digest: str = "", authored_by: str = "human",
                    bump: bool = True) -> Plan:
    """Retain input history. Runtime status changes do not create document revisions."""
    plan = store.get(Plan, plan.id) or plan
    if bump:
        plan = plan.model_copy(update={"revision": plan.revision + 1})
    payload = document(store, plan)
    text = render_plan_document(payload)
    input_digest = digest(text)
    revision = PlanRevision(
        id=f"{plan.id}:{plan.revision}", workspace_id=plan.workspace_id,
        project_id=plan.project_id, plan_id=plan.id, revision=plan.revision,
        document=payload, source=text if source is None else source, input_digest=input_digest,
        submission_digest=submission_digest, authored_by=authored_by,
    )
    store.put(revision)
    def accept(saved: Plan) -> None:
        saved.revision = plan.revision
        saved.input_digest = input_digest
    return store.update(Plan, plan.id, accept)


def revision_record(store, plan: Plan, revision: int | None = None) -> PlanRevision:
    number = plan.revision if revision is None else revision
    record = store.get(PlanRevision, f"{plan.id}:{number}")
    if record is None:
        raise ValueError(f"plan {plan.id} has no retained revision {number}")
    return record


def _apply_submission(store, project, submission: PlanSubmission) -> Plan:
    """Materialize a prepared document using stable keys; commit the revision pointer last."""
    from . import plans

    payload = submission.document
    plan = store.get(Plan, submission.plan_id)
    if plan is None:
        plan = store.put(Plan(id=submission.plan_id, workspace_id=project.workspace_id,
                              project_id=project.id, goal=payload["goal"], revision=0,
                              proposed_by=submission.authored_by))
    if plan.revision < submission.revision:
        shared_changed = any(getattr(plan, field) != payload[field]
                             for field in ("goal", "context_documents", "repo_baselines"))
        current = {item.key or item.id: item for item in plans.plan_items(store, plan)}
        for order, fields in enumerate(payload["items"]):
            item = current.get(fields["key"])
            if item is None:
                store.put(PlanItem(
                    id=digest(f"{plan.id}:{fields['key']}")[:12], workspace_id=project.workspace_id,
                    project_id=project.id, plan_id=plan.id, order=order,
                    authored_by=submission.authored_by, **fields,
                ))
            elif shared_changed or item.order != order or any(getattr(item, key) != value for key, value in fields.items()):
                def apply(saved: PlanItem) -> None:
                    for key, value in fields.items():
                        setattr(saved, key, value)
                    saved.order = order
                    if saved.status in (PlanItemStatus.queued, PlanItemStatus.approved):
                        saved.status = PlanItemStatus.proposed
                    saved.edited_by_human = submission.authored_by == "human"
                    saved.updated_at = plans.now_s()
                store.update(PlanItem, item.id, apply)
        text = render_plan_document(payload)
        input_digest = digest(text)
        store.put(PlanRevision(
            id=f"{plan.id}:{submission.revision}", workspace_id=project.workspace_id,
            project_id=project.id, plan_id=plan.id, revision=submission.revision,
            document=payload, source=submission.source, input_digest=input_digest,
            submission_digest=submission.submission_digest, authored_by=submission.authored_by,
        ))
        if submission.revision == 1:
            def consume_goal(saved: Project) -> None:
                if saved.pending_iteration_goal == submission.pending_goal:
                    saved.pending_iteration_goal = ""
                saved.goal_complete = False
                saved.goal_complete_note = ""
            store.update(Project, project.id, consume_goal)
        def accept(saved: Plan) -> None:
            saved.goal = payload["goal"]
            saved.context_documents = payload["context_documents"]
            saved.repo_baselines = payload["repo_baselines"]
            saved.revision = submission.revision
            saved.input_digest = input_digest
        plan = store.update(Plan, plan.id, accept)
    store.delete(PlanSubmission, submission.id)
    return plan


def recover_submissions(store, project, *, plan_id: str | None = None) -> None:
    from . import plans

    with plans.lock:
        for submission in store.list(PlanSubmission, workspace_id=project.workspace_id,
                                      project_id=project.id):
            if plan_id is None or submission.plan_id == plan_id:
                _apply_submission(store, project, submission)


def submit(store, project, source: str, *, authored_by: str = "human",
           append: bool = False) -> tuple[Plan, int]:
    """Accept a file once, or apply a revision-checked amendment to the same plan."""
    from . import plans

    submitted = PlanDocument.model_validate(parse_plan_document(source))
    if not submitted.items:
        raise ValueError("a submitted plan needs at least one item")
    submission_digest = digest(("append\n" if append else "") + source)
    with plans.lock:
        recover_submissions(store, project)
        previous = next((record for record in store.list(
            PlanRevision, workspace_id=project.workspace_id, project_id=project.id,
            submission_digest=submission_digest,
        )), None)
        if previous is not None:
            return store.get(Plan, previous.plan_id), previous.revision
        if append:
            live = plans.active_plan(store, project)
            if live is None:
                raise ValueError("there is no live plan to append to")
            if submitted.goal != live.goal:
                raise ValueError("an appended document must use the live plan's goal")
            if submitted.plan_id and (submitted.plan_id != live.id or submitted.revision != live.revision):
                raise RevisionConflict("append identifies a different plan or stale revision; export the current plan")
            if not submitted.plan_id and submitted.revision:
                raise ValueError("append revision requires a plan_id")
            for field in ("context_documents", "repo_baselines"):
                supplied = getattr(submitted, field)
                if supplied and supplied != getattr(live, field):
                    raise ValueError(f"append cannot change shared {field}; amend a full exported document")
            combined = document(store, live)
            combined["items"].extend(
                item.model_dump() | {"key": f"append-{submission_digest[:12]}-{index + 1}"}
                for index, item in enumerate(submitted.items)
            )
            submitted = PlanDocument.model_validate(combined)
        if not submitted.plan_id:
            if submitted.revision:
                raise ValueError("a new plan must use revision 0")
            if plans.active_plan(store, project) is not None:
                raise RevisionConflict("a live plan exists; export it and submit an amendment, or abandon it")
            payload = submitted.model_dump() | {"plan_id": new_id(), "revision": 1}
            for item in payload["items"]:
                item["repo"] = item["repo"] or project.spec_repo
            return _prepare_and_apply(store, project, payload, source, submission_digest, authored_by), 1
        plan = store.get(Plan, submitted.plan_id)
        if plan is None or plan.project_id != project.id or plan.workspace_id != project.workspace_id:
            raise ValueError("the document's plan does not belong to the selected project")
        if submitted.revision != plan.revision:
            raise RevisionConflict(
                f"stale plan revision {submitted.revision}; current revision is {plan.revision}; "
                f"export plan {plan.id} and reconcile your changes"
            )
        if plan.status not in (PlanStatus.draft, PlanStatus.approved):
            raise ValueError(f"plan is {plan.status}; submit a new plan for further work")
        current_items = plans.plan_items(store, plan)
        current = {item.key or item.id: item for item in current_items}
        incoming = {item.key: item.model_dump() for item in submitted.items}
        missing = current.keys() - incoming.keys()
        if missing:
            raise ValueError("omitted items do not imply cancellation; retain them and use plan-item-cancel: "
                             + ", ".join(sorted(missing)))
        changes = []
        for order, fields in enumerate(incoming.values()):
            if not fields["repo"]:
                fields["repo"] = project.spec_repo
            item = current.get(fields["key"])
            changed = item is None or item.order != order or any(
                fields[field] != getattr(item, field) for field in ITEM_FIELDS
            )
            if changed and item is not None and item.status in (*PLAN_ITEM_IN_FLIGHT, *PLAN_ITEM_TERMINAL):
                raise ValueError(f"item '{item.title}' is {item.status}; its instructions cannot change")
            if changed:
                changes.append((item, fields, order))
        global_change = (submitted.goal != plan.goal or submitted.context_documents != plan.context_documents
                         or submitted.repo_baselines != plan.repo_baselines)
        if global_change and any(item.status in (*PLAN_ITEM_IN_FLIGHT, *PLAN_ITEM_TERMINAL)
                                 for item in current_items):
            raise ValueError("shared goal/context cannot change after an item starts; amend future item notes")
        if not changes and not global_change:
            return plan, plan.revision
        payload = submitted.model_dump() | {"revision": plan.revision + 1, "items": list(incoming.values())}
        plan = _prepare_and_apply(store, project, payload, source, submission_digest, authored_by)
        return plan, plan.revision


def _prepare_and_apply(store, project, payload, source, submission_digest, authored_by):
    submission = store.put(PlanSubmission(
        id=digest(f"{project.workspace_id}:{project.id}:{submission_digest}"),
        workspace_id=project.workspace_id, project_id=project.id, plan_id=payload["plan_id"],
        revision=payload["revision"], document=payload, source=source,
        submission_digest=submission_digest, authored_by=authored_by,
        pending_goal=project.pending_iteration_goal,
    ))
    return _apply_submission(store, project, submission)
