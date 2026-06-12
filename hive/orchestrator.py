"""The AI orchestrator: a Gemini tool-loop invoked per event by the supervisor.

Statelessness contract: durable knowledge lives in the spec repo and the store;
the conversation history (kept in the blob store) is an optimization that can
be lost at any time — every invocation also receives a full state snapshot.
"""

# No `from __future__ import annotations` here: google-genai automatic function
# calling inspects runtime type hints of the tool methods; stringified
# annotations break its schema inference.

import json
import logging
from pathlib import Path

from hive.models import (
    HumanTask,
    HumanTaskStatus,
    Project,
    Question,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
    Workstream,
    WorkstreamStatus,
)
from hive.prompts import load as load_prompt
from hive.specrepo import SpecRepo

log = logging.getLogger("hive.orchestrator")

HISTORY_LIMIT = 80
RESULT_SNIPPET = 4000
BACKENDS = ("claude", "cursor", "codex", "gemini-cli")


class Tools:
    """Tool implementations the model calls. Methods returned by `functions()`
    are exposed to google-genai automatic function calling; their docstrings
    are the tool descriptions the model sees."""

    def __init__(self, store, project: Project, spec: SpecRepo | None) -> None:
        self.store = store
        self.project = project
        self.spec = spec
        self.actions: list[str] = []

    def functions(self) -> list:
        return [
            self.create_workstream,
            self.create_task,
            self.ask_user,
            self.park_workstream,
            self.reactivate_workstream,
            self.complete_workstream,
            self.commit_to_spec,
            self.create_human_task,
            self.mark_goal_complete,
        ]

    # -- tools ---------------------------------------------------------------

    def create_workstream(self, title: str, description: str) -> str:
        """Create a workstream: a coarse direction of work (e.g. 'auth flow')
        touching a mostly-disjoint part of the codebase."""
        ws = self.store.put(
            Workstream(project_id=self.project.id, title=title, description=description)
        )
        self.actions.append(f"created workstream {ws.id} '{title}'")
        return f"workstream_id={ws.id}"

    def create_task(
        self,
        workstream_id: str,
        repo: str,
        instructions: str,
        backend: str = "cursor",
        kind: str = "work",
    ) -> str:
        """Queue a task for a coding agent. repo is the git URL to check out.
        kind is 'work' (implements, then lands changes) or 'verify' (fresh-eyes
        review of the previous task; landing is disabled). backend is one of
        claude | cursor | codex | gemini-cli — pick one that an online runner
        advertises (see RUNNERS in the snapshot), or the task cannot dispatch."""
        if backend not in BACKENDS:
            return f"error: unknown backend {backend!r}, use one of {BACKENDS}"
        online = [b for r in self.store.list(Runner) if r.online() for b in r.backends]
        if online and backend not in online:
            return f"error: no online runner offers {backend!r}; available now: {sorted(set(online))}"
        if not self.store.get(Workstream, workstream_id):
            return f"error: no workstream {workstream_id}"
        prompt_versions = {}
        if kind == TaskKind.work:
            landing_name = (
                "landing_direct_push" if self.project.autonomy == "direct_push" else "landing_pr"
            )
            landing, version = load_prompt(landing_name)
            instructions = f"{instructions}\n\n{landing}"
            prompt_versions[landing_name] = version
        else:
            suffix, version = load_prompt("verify_suffix")
            instructions = f"{instructions}\n\n{suffix}"
            prompt_versions["verify_suffix"] = version
        task = self.store.put(
            Task(
                project_id=self.project.id,
                workstream_id=workstream_id,
                repo=repo,
                kind=TaskKind(kind),
                instructions=instructions,
                backend=backend,
                prompt_versions=prompt_versions,
            )
        )
        self.actions.append(f"queued {kind} task {task.id} on {repo} via {backend}")
        return f"task_id={task.id} (queued)"

    def ask_user(self, question_markdown: str, workstream_id: str = "") -> str:
        """Ask the human a clarification question (markdown: context, the
        gap/contradiction, options, your recommendation). If workstream_id is
        given, that workstream is parked until the answer arrives."""
        q = self.store.put(
            Question(
                project_id=self.project.id,
                workstream_id=workstream_id,
                text=question_markdown,
            )
        )
        if workstream_id and (ws := self.store.get(Workstream, workstream_id)):
            ws.status = WorkstreamStatus.parked
            ws.parked_reason = f"awaiting answer to question {q.id}"
            self.store.put(ws)
        self.actions.append(f"asked user question {q.id}")
        return f"question_id={q.id} (user will see it in the inbox)"

    def park_workstream(self, workstream_id: str, reason: str) -> str:
        """Park a workstream (stop working on it) with a reason."""
        ws = self.store.get(Workstream, workstream_id)
        if not ws:
            return f"error: no workstream {workstream_id}"
        ws.status = WorkstreamStatus.parked
        ws.parked_reason = reason
        self.store.put(ws)
        self.actions.append(f"parked workstream {workstream_id}: {reason}")
        return "parked"

    def reactivate_workstream(self, workstream_id: str) -> str:
        """Reactivate a parked workstream."""
        ws = self.store.get(Workstream, workstream_id)
        if not ws:
            return f"error: no workstream {workstream_id}"
        ws.status = WorkstreamStatus.active
        ws.parked_reason = ""
        self.store.put(ws)
        self.actions.append(f"reactivated workstream {workstream_id}")
        return "active"

    def complete_workstream(self, workstream_id: str) -> str:
        """Mark a workstream done (its goal is built and verified)."""
        ws = self.store.get(Workstream, workstream_id)
        if not ws:
            return f"error: no workstream {workstream_id}"
        ws.status = WorkstreamStatus.done
        self.store.put(ws)
        self.actions.append(f"completed workstream {workstream_id}")
        return "done"

    def commit_to_spec(self, files_json: str, message: str) -> str:
        """Write files to the project's spec repo and push. files_json is a
        JSON object mapping relative path -> full file content, e.g.
        {"wiki/decisions.md": "...", "input-log/2026-06-12-auth.md": "..."}.
        Use for distilled wiki updates and raw user-input logs."""
        if self.spec is None:
            return "error: spec repo unavailable this invocation"
        files = json.loads(files_json)
        sha = self.spec.commit_files(files, message)
        self.actions.append(f"committed to spec repo: {message} ({sha[:8]})")
        return f"committed {sha[:8]}"

    def create_human_task(self, title: str, instructions_markdown: str) -> str:
        """File a todo for the human operator: an action only they can perform
        outside the system — CLI logins on runner machines, DNS records, billing,
        granting access. Give exact copy-pasteable commands/steps. Unlike
        ask_user this requests an action, not an answer. Check OPEN HUMAN TODOS
        in the snapshot first to avoid duplicates."""
        t = self.store.put(HumanTask(title=title, instructions=instructions_markdown))
        self.actions.append(f"filed human todo {t.id} '{title}'")
        return f"human_task_id={t.id} (user will see it on the resources page)"

    def mark_goal_complete(self, summary: str) -> str:
        """Declare the iteration goal fully built and verified. Only valid once
        every workstream is done/parked, no tasks are queued or running, and no
        questions are open. The project goes idle until the human sets the next
        goal."""
        unfinished = [
            t
            for t in self.store.list(Task, project_id=self.project.id)
            if t.status in (TaskStatus.pending, TaskStatus.running)
        ]
        active = self.store.list(
            Workstream, project_id=self.project.id, status=WorkstreamStatus.active
        )
        open_questions = self.store.open_questions(self.project.id)
        if unfinished or active or open_questions:
            return (
                f"rejected: {len(unfinished)} unfinished tasks, {len(active)} active "
                f"workstreams, {len(open_questions)} open questions. Finish or park them first."
            )
        self.project.goal_complete = True
        self.project.goal_complete_note = summary
        self.store.put(self.project)
        self.actions.append("marked goal complete")
        return "goal marked complete"

    # -- context -------------------------------------------------------------

    def snapshot(self) -> str:
        ws_lines = []
        for ws in self.store.list(Workstream, project_id=self.project.id):
            line = f"- [{ws.status}] {ws.id} '{ws.title}': {ws.description}"
            if ws.parked_reason:
                line += f" (parked: {ws.parked_reason})"
            ws_lines.append(line)
        task_lines = []
        tasks = self.store.list(Task, project_id=self.project.id)
        for t in tasks[-15:]:
            line = f"- [{t.status}] {t.kind} task {t.id} ws={t.workstream_id} repo={t.repo} backend={t.backend}"
            if t.status in (TaskStatus.done, TaskStatus.failed):
                line += f"\n  result: {t.result_text[:RESULT_SNIPPET]}"
            task_lines.append(line)
        q_lines = [
            f"- [{q.status}] {q.id}: {q.text[:500]}" + (f"\n  answer: {q.answer}" if q.answer else "")
            for q in self.store.list(Question, project_id=self.project.id)[-10:]
        ]
        runner_lines = [
            f"- {r.name}: backends={','.join(r.backends)} {'online' if r.online() else 'OFFLINE'}"
            for r in self.store.list(Runner)
        ]
        todo_lines = [
            f"- {t.id}: {t.title}"
            for t in self.store.list(HumanTask, status=HumanTaskStatus.open)
        ]
        p = self.project
        return "\n".join(
            [
                f"PROJECT {p.name} | mode={p.mode} autonomy={p.autonomy} "
                f"guess_propensity={p.guess_propensity} goal_complete={p.goal_complete}",
                f"member repos: {', '.join(p.member_repos) or '(none)'}",
                f"spec repo: {p.spec_repo}",
                "",
                "WORKSTREAMS:",
                *(ws_lines or ["(none yet)"]),
                "",
                "RECENT TASKS:",
                *(task_lines or ["(none)"]),
                "",
                "QUESTIONS:",
                *(q_lines or ["(none)"]),
                "",
                "RUNNERS:",
                *(runner_lines or ["(none online — tasks will wait)"]),
                "",
                "OPEN HUMAN TODOS (org-wide):",
                *(todo_lines or ["(none)"]),
            ]
        )


class Orchestrator:
    def __init__(self, store, blobs, config) -> None:
        self.store = store
        self.blobs = blobs
        self.config = config

    def invoke(self, project_id: str, events: list[str]) -> None:
        project = self.store.get(Project, project_id)
        if project is None:
            return
        spec: SpecRepo | None = SpecRepo(
            project.spec_repo, Path(self.config.data_dir) / "specs", self.config.gh_token
        )
        spec_digest = ""
        try:
            spec.sync()
            spec_digest = spec.digest()
        except Exception as exc:
            log.warning("spec repo sync failed: %s", exc)
            spec_digest = f"(spec repo unavailable: {exc})"
            spec = None

        tools = Tools(self.store, project, spec)
        event_text = "\n".join(f"- {e}" for e in events)
        user_msg = (
            f"EVENTS:\n{event_text}\n\nSTATE SNAPSHOT:\n{tools.snapshot()}"
            f"\n\nSPEC:\n{spec_digest}"
        )
        history = self._load_history(project_id)
        final_text = self._generate(project, history, user_msg, tools)
        log.info("orchestrator[%s]: %s | actions: %s", project.name, final_text[:200], tools.actions)

        # Persist model text only — echoing executed tool calls as text teaches
        # the model to narrate actions instead of calling tools. The snapshot in
        # the next invocation is the ground truth for what actually happened.
        history.append({"role": "user", "text": f"EVENTS:\n{event_text}"})
        history.append({"role": "model", "text": final_text})
        self._save_history(project_id, history[-HISTORY_LIMIT:])

    # -- LLM call (overridden in tests) ---------------------------------------

    def _generate(self, project: Project, history: list[dict], user_msg: str, tools: Tools) -> str:
        from google import genai
        from google.genai import types

        base_prompt, _version = load_prompt("orchestrator")
        org_context = self.store.get_org_context()
        system = base_prompt + (f"\n\nORG CONTEXT:\n{org_context}" if org_context else "")
        contents = [
            types.Content(role=m["role"], parts=[types.Part(text=m["text"])]) for m in history
        ]
        contents.append(types.Content(role="user", parts=[types.Part(text=user_msg)]))
        client = genai.Client(api_key=self.config.gemini_api_key)
        response = client.models.generate_content(
            model=self.config.orch_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=tools.functions(),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=25
                ),
            ),
        )
        return response.text or "(no text)"

    # -- history persistence ---------------------------------------------------

    def _history_blob(self, project_id: str) -> str:
        return f"orchestrator-context/{project_id}.json"

    def _load_history(self, project_id: str) -> list[dict]:
        raw = self.blobs.get(self._history_blob(project_id))
        if raw is None:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("corrupt orchestrator history for %s — cold start", project_id)
            return []

    def _save_history(self, project_id: str, history: list[dict]) -> None:
        self.blobs.put(self._history_blob(project_id), json.dumps(history).encode())
