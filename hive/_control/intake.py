"""Project intake: the trusted-scout conversation that aligns a project with the
user and pushes durable spec files before planning starts.

These were inline closures in `hive.api`; kept here as free functions taking the
store/config/supervisor explicitly, matching the workstream modules. Like
`hive._integrations.auth`, this layer raises `HTTPException` directly so the
route handlers stay thin.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import HTTPException

from hive.config.settings import Config
from hive._integrations.specrepo import REQUIRED_INTAKE_FILES, SpecRepo, SpecStatus, spec_status_dir
from hive.models import (
    AgentConversation,
    ConversationStatus,
    Project,
    ProjectState,
    Resource,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
)

# Intake is high leverage, so only trusted backends qualify (preference order).
TRUSTED_SCOUTS = (("codex", "gpt-5.5"), ("claude", "opus"))


def trusted_capacity(store, workspace_id: str, prefer_backend: str = "") -> tuple[str, str, str]:
    """Return (backend, model, runner_id) for an available trusted intake scout.

    `prefer_backend` pins the choice when that backend is available (the user
    explicitly picked it on retry); otherwise the first available backend in
    preference order is used, so a project is never stuck because the default
    scout is blocked while another trusted one is ready.
    """
    online = {r.id: r for r in store.list(Runner, workspace_id=workspace_id) if r.online()}
    ordered = sorted(
        TRUSTED_SCOUTS, key=lambda bm: (bm[0] != prefer_backend, TRUSTED_SCOUTS.index(bm))
    )
    if prefer_backend and prefer_backend not in dict(TRUSTED_SCOUTS):
        raise HTTPException(
            400,
            f"unknown trusted scout {prefer_backend!r}; choose one of "
            f"{', '.join(b for b, _ in TRUSTED_SCOUTS)}",
        )
    for backend, model in ordered:
        for resource in store.list(Resource, workspace_id=workspace_id, backend=backend):
            runner = online.get(resource.runner_id)
            if runner and backend in runner.backends and resource.available():
                return backend, model, runner.id
    raise HTTPException(
        409,
        "intake requires a usable trusted scout backend (codex gpt-5.5 or claude opus); "
        "probe or fix a trusted scout, then retry",
    )


def spec_status(config: Config, project: Project) -> SpecStatus:
    if not project.spec_repo.strip():
        return SpecStatus((), (), (), False, "spec_repo is not set")
    try:
        local = Path(project.spec_repo)
        is_bare_git = (
            local.exists()
            and local.is_dir()
            and (local / "HEAD").is_file()
            and (local / "objects").is_dir()
        )
        if local.exists() and local.is_dir() and not is_bare_git:
            return spec_status_dir(local)
        repo = SpecRepo(project.spec_repo, Path(config.data_dir) / "specs", config.gh_token)
        repo.sync()
        return spec_status_dir(repo.path)
    except Exception as exc:
        return SpecStatus(REQUIRED_INTAKE_FILES, (), REQUIRED_INTAKE_FILES, False, str(exc))


def require_spec_files_ready(config: Config, project: Project) -> SpecStatus:
    status = spec_status(config, project)
    if status.ready:
        return status
    if status.error:
        raise HTTPException(409, f"could not verify spec files: {status.error}")
    raise HTTPException(
        409,
        "missing required spec files: " + ", ".join(status.missing_files),
    )


def create_conversation(store, project: Project, prefer_backend: str = "") -> AgentConversation:
    backend, model, _runner_id = trusted_capacity(store, project.workspace_id, prefer_backend)
    conversation = store.put(
        AgentConversation(
            workspace_id=project.workspace_id,
            project_id=project.id,
            repo=project.spec_repo,
            backend=backend,
            model=model,
            status=ConversationStatus.open,
        )
    )
    project.intake_conversation_id = conversation.id
    project.state = ProjectState.intake
    store.put(project)
    return conversation


def writable_conversation(store, project: Project, prefer_backend: str = "") -> AgentConversation:
    existing = (
        store.get(AgentConversation, project.intake_conversation_id)
        if project.intake_conversation_id
        else None
    )
    if existing and existing.status in (ConversationStatus.running, ConversationStatus.finalizing):
        return existing
    if existing and existing.status == ConversationStatus.open:
        return existing
    return create_conversation(store, project, prefer_backend)


def accept(
    store,
    supervisor,
    config: Config,
    project: Project,
    conversation: AgentConversation | None = None,
) -> tuple[AgentConversation, SpecStatus]:
    if conversation and conversation.status in (ConversationStatus.running, ConversationStatus.finalizing):
        raise HTTPException(409, "intake scout is already running")
    status = require_spec_files_ready(config, project)
    accepted_at = time.time()
    summary = (
        "Accepted durable spec files: "
        + ", ".join(status.present_files)
        + ". Planning can use the spec repo as source of truth."
    )
    if conversation:
        def mark(conv: AgentConversation) -> None:
            conv.status = ConversationStatus.done
            conv.latest_brief = conv.latest_brief or summary
            conv.transcript.append({"role": "system", "text": summary})
            conv.updated_at = accepted_at

        accepted_conv = store.update(AgentConversation, conversation.id, mark) or conversation
    else:
        accepted_conv = store.put(
            AgentConversation(
                workspace_id=project.workspace_id,
                project_id=project.id,
                repo=project.spec_repo,
                backend="manual",
                model="",
                status=ConversationStatus.done,
                latest_brief=summary,
                transcript=[{"role": "system", "text": summary}],
                updated_at=accepted_at,
            )
        )
    project.intake_conversation_id = accepted_conv.id
    project.state = ProjectState.idle
    store.put(project)
    supervisor.wake(
        project.id,
        "Intake accepted from durable spec files. Plan from the spec repo.\n"
        f"Files: {', '.join(status.present_files)}",
    )
    return accepted_conv, status


def _context(conversation: AgentConversation) -> str:
    recent = conversation.transcript[-8:]
    transcript = "\n\n".join(
        f"{item.get('role', 'unknown')}:\n{item.get('text', '').strip()}"
        for item in recent
        if item.get("text", "").strip()
    )
    return "\n".join(
        [
            "Current intake context:",
            "",
            "Latest brief:",
            conversation.latest_brief.strip() or "(none yet)",
            "",
            "Recent transcript:",
            transcript or "(none)",
            "",
        ]
    )


def prompt(
    store,
    project: Project,
    conversation: AgentConversation,
    turn: str,
    user_text: str = "",
) -> str:
    if turn == "initial":
        org_context = store.get_org_context(project.workspace_id).strip()
        return "\n".join(
            [
                "You are Hive's intake scout.",
                "",
                "Goal: understand this project well enough that the user can confirm or correct Hive before work starts.",
                "",
                "Inspect the repo. Prefer mission.md, iteration.md, and wiki/ over README guesses. "
                "You may run cheap diagnostic commands. You may browse public docs for external "
                "packages/APIs/services, but do not leak private repo content.",
                "Do not commit, push, deploy, send external messages, or create Hive workstreams/tasks.",
                "",
                f"Project name: {project.name}",
                f"Spec/code repo: {project.spec_repo}",
                f"Member repos: {', '.join(project.member_repos) or '(none)'}",
                f"Guess propensity: {project.guess_propensity}",
                "",
                "Org context:",
                org_context or "(none)",
                "",
                "Return a compact brief with these sections:",
                "",
                "Mission:",
                "The long-term vision.",
                "",
                "Next iteration:",
                "The concrete, verifiable next goal Hive should probably work toward.",
                "",
                "Likely next steps:",
                "3-5 high-level steps, not implementation tasks.",
                "",
                "Assumptions:",
                "Cheap or reasonable assumptions you made instead of asking.",
                "",
                "Questions:",
                "Only questions whose answers would materially change what Hive builds.",
                "",
                "Evidence:",
                "The files, commands, or public sources that shaped your understanding.",
            ]
        )
    if turn == "proceed":
        return (
            _context(conversation)
            + "\n"
            "The user chose to proceed with current information and accepts the risk of "
            "wrong assumptions.\n\n"
            "Return a compact updated brief using the current repo/spec context. Do not "
            "edit files, commit, push, or report on file changes. Do not ask more "
            "questions unless work would be impossible rather than merely risky. Clearly "
            "list the assumptions you are making."
        )
    if turn == "write_mission":
        return (
            _context(conversation)
            + "\n"
            "Write the durable project-intake files in the spec repo. The canonical "
            "outputs are dedicated files, not markdown sections in this chat.\n\n"
            "Edit or create:\n"
            "- mission.md — the long-term mission and operating principles.\n"
            "- iteration.md — the concrete next iteration goal and acceptance signal.\n\n"
            "You may also update wiki/intake.md and input-log/* if useful for provenance. "
            "Do not modify product code. Commit and push the spec changes. Report the "
            "commit SHA and the files changed."
        )
    return (
        _context(conversation)
        + "\n"
        "The user responded during intake:\n\n"
        f"{user_text.strip()}\n\n"
        "Update your understanding. Self-answer minor follow-ups. Return the revised "
        "brief and only the remaining material questions. Do not commit or push yet."
    )


def queue_turn(
    store,
    project: Project,
    conversation: AgentConversation,
    turn: str,
    user_text: str = "",
) -> Task:
    if any(
        t.status in (TaskStatus.pending, TaskStatus.running)
        for t in store.list(Task, workspace_id=project.workspace_id, project_id=project.id)
        if t.kind == TaskKind.intake and t.conversation_id == conversation.id
    ):
        raise HTTPException(409, "intake scout is already running")
    task = store.put(
        Task(
            workspace_id=project.workspace_id,
            project_id=project.id,
            workstream_id="",
            repo=conversation.repo,
            kind=TaskKind.intake,
            instructions=prompt(store, project, conversation, turn, user_text),
            conversation_id=conversation.id,
            conversation_turn=turn,
            session_handle=conversation.session_handle,
            backend=conversation.backend,
            model=conversation.model,
            prompt_versions={"intake": "inline-v1"},
        )
    )

    def mark(conv: AgentConversation) -> None:
        conv.status = (
            ConversationStatus.finalizing
            if turn in ("finalize", "write_mission")
            else ConversationStatus.running
        )
        conv.last_task_id = task.id
        conv.updated_at = time.time()
        if user_text.strip():
            conv.transcript.append({"role": "user", "text": user_text.strip()})

    updated = store.update(AgentConversation, conversation.id, mark)
    if updated:
        project.intake_conversation_id = updated.id
        project.state = ProjectState.intake
        store.put(project)
    return task


def is_done(store, project: Project) -> bool:
    if not project.intake_conversation_id:
        return False
    conversation = store.get(AgentConversation, project.intake_conversation_id)
    return bool(conversation and conversation.status == ConversationStatus.done)
