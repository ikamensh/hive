"""Clarification answers: persist a human's answer to a parked question, append
the raw answer to the spec repo's input-log, and wake the planner to distill it.

Intake's sibling — both write durable spec material and escalate on failure.
Lifted out of `hive.api` as free functions over store/config/supervisor; the
404 guard stays in the route, so this module needs no FastAPI.
"""

from __future__ import annotations

import datetime
import logging
import time
from pathlib import Path

from hive.config.settings import Config
from hive._control.escalation import escalate
from hive._integrations.specrepo import SpecRepo
from hive._workstreams.testability import (
    active_contract_task,
    is_decision_question,
    queue_draft_task,
)
from hive.models import HumanTaskKind, Project, ProjectWorkstream, Question, QuestionStatus

log = logging.getLogger("hive._control.clarifications")


def can_write_spec_repo(config: Config, project: Project) -> bool:
    """Whether the chief has an obvious spec-repo write path. Avoids slow surprise
    network attempts in throwaway/local runs: production has HIVE_GH_TOKEN; tests
    and smoke runs often use a filesystem path. Other remotes are handled by the
    orchestrator via commit_to_spec instead, so the chief only auto-writes when
    the path is obvious."""
    url = project.spec_repo
    return bool(config.gh_token.strip()) or url.startswith("file://") or Path(url).exists()


def _record_input_log(
    config: Config, project: Project, question: Question, answer: str, answered_at: float
) -> str:
    stamp = datetime.datetime.fromtimestamp(answered_at, datetime.UTC)
    path = f"input-log/{stamp:%Y-%m-%d-%H%M%S}-{question.id}.md"
    body = "\n".join(
        [
            f"# Clarification answer {question.id}",
            "",
            f"- Answered: {stamp.isoformat()}",
            f"- Project: {project.name} ({project.id})",
            f"- IssueItem: {question.workstream_id or 'project-level'}",
            "",
            "## Question",
            "",
            question.text.strip(),
            "",
            "## Answer",
            "",
            answer.strip(),
            "",
        ]
    )
    spec = SpecRepo(
        project.spec_repo,
        Path(config.data_dir or "/tmp/hive-data") / "specs",
        config.gh_token,
    )
    sha = spec.commit_files({path: body}, f"Record clarification answer {question.id}")
    return f"{path} @ {sha[:8]}"


def _escalate_log_failure(store, project: Project, question: Question, exc: Exception) -> None:
    escalate(
        store,
        f"Repair spec logging for {project.name}",
        instructions=(
            "Hive saved a clarification answer in the chief DB, but could not "
            "append the raw answer to the spec repo input log.\n\n"
            f"Question: `{question.id}`\n\n"
            f"Spec repo: `{project.spec_repo}`\n\n"
            f"Error:\n\n```\n{type(exc).__name__}: {str(exc)[:1500]}\n```\n\n"
            "Fix spec-repo write access, then ask Hive to distill or replay the answer "
            "from the project question history."
        ),
        project_id=project.id,
        workspace_id=project.workspace_id,
        kind=HumanTaskKind.repair,
        dedup_key=f"repair:spec-log:{project.id}",
    )


def _fold_testability_answer(store, config: Config, project: Project, question: Question) -> str:
    """The user answered a testability decision — the editing is Hive's job:
    queue a draft task that folds every settled answer into `testability.md`
    (and re-proves it via the draft→probe chain)."""
    if not is_decision_question(question) or not question.workstream_id:
        return ""
    workstream = store.get(ProjectWorkstream, question.workstream_id)
    if not workstream:
        return ""
    if active_contract_task(store, project, workstream):
        return "A testability task is already in flight; the answer folds in on the next draft.\n"
    task = queue_draft_task(
        store,
        project,
        workstream,
        backend=config.test_refresh_backend,
        model=config.test_refresh_model,
    )
    return f"Queued testability draft {task.id} to fold the answer into testability.md.\n"


def apply_answer(
    store, supervisor, config: Config, project: Project, question: Question, answer: str
) -> Question:
    """Persist the answer, best-effort append it to the spec input-log (escalating
    a todo on write failure), and wake the planner to distill it."""
    answered_at = time.time()
    input_log_note = ""
    if can_write_spec_repo(config, project):
        try:
            input_log_note = (
                "Chief already appended the raw answer to "
                f"{_record_input_log(config, project, question, answer, answered_at)}.\n"
            )
        except Exception as exc:
            log.warning("failed to append question %s to spec input-log: %s", question.id, exc)
            _escalate_log_failure(store, project, question, exc)
            input_log_note = (
                "Chief could not append the raw answer to input-log automatically; "
                "a human todo was filed with the write error.\n"
            )
    question.status = QuestionStatus.answered
    question.answer = answer
    question.answered_at = answered_at
    store.put(question)
    redraft_note = _fold_testability_answer(store, config, project, question)
    supervisor.wake(
        question.project_id,
        f"User answered question {question.id}.\nQ: {question.text}\nA: {answer}\n"
        f"{input_log_note}{redraft_note}"
        "Distill this into the wiki/spec and continue.",
    )
    return question


def dismiss(store, supervisor, question: Question) -> Question:
    """Mark the question dismissed and nudge the planner to re-decide any
    workstream that parked on it."""
    question.status = QuestionStatus.dismissed
    store.put(question)
    supervisor.wake(
        question.project_id,
        f"User dismissed question {question.id} without answering. If a workstream "
        "parked on it, decide whether to reactivate it or leave it parked.",
    )
    return question
