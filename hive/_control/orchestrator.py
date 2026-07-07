"""The AI orchestrator: an LLM tool-loop invoked per event by the supervisor.

Statelessness contract: durable knowledge lives in the spec repo and the store;
the conversation history (kept in the blob store) is an optimization that can
be lost at any time — every invocation also receives a full state snapshot.
"""

# No `from __future__ import annotations` here: provider tool-schema generation
# inspects runtime type hints of the tool methods; stringified annotations break
# google-genai schema inference in particular.

import json
import logging
import time
from pathlib import Path

from hive.agents import BACKEND_NAMES, REGISTRY
from hive._control.escalation import escalate
from hive._workstreams.issues import ensure_iteration_workstream
from hive.llm import LoopResult, ProviderUnavailable, ToolLoop, ToolSet, build_adapters
from hive.models import (
    Autonomy,
    Feedback,
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    OrchestratorRun,
    Project,
    Question,
    QuestionStatus,
    Resource,
    Runner,
    Subscription,
    Task,
    TaskKind,
    TaskStatus,
    Verdict,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)
from hive.llm._pricing import estimate_cost
from hive.llm.prompts import load as load_prompt
from hive._integrations.specrepo import SpecRepo

log = logging.getLogger("hive._control.orchestrator")

HISTORY_LIMIT = 80
RESULT_SNIPPET = 4000
MAX_FIX_ROUNDS = 3  # consecutive verify rejects before a workstream must park + ask
MAX_REMOTE_CALLS = 25  # tool-call rounds per orchestrator invocation


def _structured_question_problem(question_markdown: str) -> str:
    lower = question_markdown.strip().lower()
    missing = []
    if "option" not in lower:
        missing.append("options")
    if "recommend" not in lower:
        missing.append("a recommendation")
    if missing:
        return (
            "error: ask_user requires structured markdown with context, the gap or "
            f"contradiction, options, and a recommendation; missing {', '.join(missing)}."
        )
    return ""


class Tools:
    """Tool implementations the model calls. Methods returned by `functions()`
    are exposed to the configured provider; their docstrings are the tool
    descriptions the model sees."""

    def __init__(
        self, store, project: Project, spec: SpecRepo | None, gh_token: str = ""
    ) -> None:
        self.store = store
        self.project = project
        self.spec = spec
        self.gh_token = gh_token
        self.actions: list[str] = []

    def functions(self) -> list:
        return [
            self.create_workstream,
            self.create_task,
            self.cancel_task,
            self.ask_user,
            self.withdraw_question,
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
        iteration = ensure_iteration_workstream(self.store, self.project)
        ws = self.store.put(
            Workstream(
                workspace_id=self.project.workspace_id,
                project_id=self.project.id,
                workstream_id=iteration.id,
                title=title,
                description=description,
            )
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
        claude | cursor | codex | gemini-cli — pick one with a *usable* agent
        on an online machine (see RUNNERS in the snapshot), or the task cannot
        dispatch."""
        if backend not in BACKEND_NAMES:
            return f"error: unknown backend {backend!r}, use one of {BACKEND_NAMES}"
        online_runners = {
            r.id
            for r in self.store.list(Runner, workspace_id=self.project.workspace_id)
            if r.online()
        }
        usable = sorted(
            {
                res.backend
                for res in self.store.list(Resource, workspace_id=self.project.workspace_id)
                if res.available() and res.runner_id in online_runners
            }
        )
        if usable and backend not in usable:
            # Installed is not usable: a parked/failed/cooling-down agent must
            # not receive work (live: two cursor tasks queued against a dead
            # subscription before this gate existed).
            return (
                f"error: no usable {backend!r} agent is available right now "
                f"(offline, parked, failed, or cooling down); pick from: {usable}"
            )
        ws = self.store.get(Workstream, workstream_id)
        if not ws:
            return f"error: no workstream {workstream_id}"
        if ws.source == WorkstreamSource.issue:
            return (
                "error: GitHub issue work items are owned by the deterministic "
                "issue pipeline. Use the Issues view to scan/run issues instead."
            )
        if not ws.repo and repo:
            ws.repo = repo
            self.store.put(ws)
        if kind == TaskKind.work and self._unresolved_rejects(workstream_id) >= MAX_FIX_ROUNDS:
            return (
                f"error: workstream {workstream_id} has {MAX_FIX_ROUNDS} verify rejects with no "
                "accept since. Don't queue another fix — park the workstream and ask the user "
                "what to change (park_workstream + ask_user)."
            )
        if kind == TaskKind.work and self._failed_work_streak(workstream_id) >= MAX_FIX_ROUNDS:
            return (
                f"error: workstream {workstream_id} has {MAX_FIX_ROUNDS} work tasks that failed "
                "(runner/execution errors) with no success since. Re-running won't help — park "
                "the workstream and ask the user (try a different backend or fix the blocker)."
            )
        # PR (mature) mode keeps each workstream's work on its own branch so the
        # verify task reviews exactly those changes and a human merges the PR.
        # direct_push (fast) mode lands on the default branch; verify is the
        # after-the-fact safety net that triggers a fix on reject.
        branch = f"hive/{workstream_id[:8]}" if self.project.autonomy == Autonomy.pr else ""
        prompt_versions = {}
        if kind == TaskKind.work:
            landing_name = (
                "landing_direct_push" if self.project.autonomy == Autonomy.direct_push else "landing_pr"
            )
            landing, version = load_prompt(landing_name)
            instructions = f"{instructions}\n\n{landing}"
            if branch:
                instructions += f"\n\nUse the git branch `{branch}` for this work."
            prompt_versions[landing_name] = version
        else:
            suffix, version = load_prompt("verify_suffix")
            instructions = f"{instructions}\n\n{suffix}"
            prompt_versions["verify_suffix"] = version
        task = self.store.put(
            Task(
                workspace_id=self.project.workspace_id,
                project_id=self.project.id,
                workstream_id=workstream_id,
                work_item_id=workstream_id,
                repo=repo,
                branch=branch,
                kind=TaskKind(kind),
                instructions=instructions,
                backend=backend,
                prompt_versions=prompt_versions,
            )
        )
        self.actions.append(f"queued {kind} task {task.id} on {repo} via {backend}")
        return f"task_id={task.id} (queued)"

    def _unresolved_rejects(self, workstream_id: str) -> int:
        """Verify rejects since the last accept in a workstream — the fix-loop
        depth the orchestrator must not exceed before escalating to the human."""
        count = 0
        for t in self.store.list(Task, project_id=self.project.id, workstream_id=workstream_id):
            if t.kind != TaskKind.verify:
                continue
            if t.verdict == Verdict.accept:
                count = 0
            elif t.verdict == Verdict.reject:
                count += 1
        return count

    def _failed_work_streak(self, workstream_id: str) -> int:
        """Consecutive failed work tasks since the last successful one — guards
        against re-queueing work that keeps crashing (bad creds, runtime errors)
        instead of failing quality review, which `_unresolved_rejects` covers."""
        streak = 0
        for t in self.store.list(Task, project_id=self.project.id, workstream_id=workstream_id):
            if t.kind != TaskKind.work:
                continue
            if t.status == TaskStatus.failed:
                streak += 1
            elif t.status == TaskStatus.done:
                streak = 0
        return streak

    def cancel_task(self, task_id: str, reason: str) -> str:
        """Cancel one of this project's own queued or running tasks — e.g. work
        stuck pending on a backend that turned out unusable. A pending task
        stops immediately; a running one is flagged and the runner stops it
        cooperatively. Never ask the human to cancel a task: this tool is how
        you do it yourself. Deterministic pipeline tasks (issue resolve/review,
        testing) are owned by their workstreams and cannot be cancelled here."""
        task = self.store.get(Task, task_id)
        if not task or task.project_id != self.project.id:
            return f"error: no task {task_id} in this project"
        if task.kind not in (TaskKind.work, TaskKind.verify):
            return f"error: {task.kind} tasks belong to their pipeline; cancel only work/verify tasks"
        if task.status == TaskStatus.running and task.delivered:
            task.cancel_requested = True
            self.store.put(task)
            self.actions.append(f"requested cancel of running task {task_id}: {reason}")
            return "cancel requested; the runner will stop it and report the result"
        if task.status in (TaskStatus.pending, TaskStatus.running):
            task.status = TaskStatus.cancelled
            task.result_text = f"Cancelled by the planner: {reason}"
            task.finished_at = time.time()
            self.store.put(task)
            self.actions.append(f"cancelled task {task_id}: {reason}")
            return "cancelled"
        return f"error: task is already {task.status}; nothing to cancel"

    def ask_user(self, question_markdown: str, workstream_id: str = "") -> str:
        """Ask the human a clarification question (markdown: context, the
        gap/contradiction, options, your recommendation). If workstream_id is
        given, that workstream is parked until the answer arrives."""
        if problem := _structured_question_problem(question_markdown):
            return problem
        q = self.store.put(
            Question(
                workspace_id=self.project.workspace_id,
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

    def withdraw_question(self, question_id: str, reason: str) -> str:
        """Withdraw one of your own open questions that events made moot — newer
        information answered it, or the decision no longer matters. The user
        should never have to answer a stale question just so the project can
        complete. Do not withdraw a question that still gates a real decision."""
        q = self.store.get(Question, question_id)
        if not q or q.project_id != self.project.id:
            return f"error: no question {question_id} in this project"
        if q.status != QuestionStatus.open:
            return f"error: question is already {q.status}"
        q.status = QuestionStatus.withdrawn
        q.answer = f"(withdrawn by the planner: {reason})"
        self.store.put(q)
        self.actions.append(f"withdrew question {question_id}: {reason}")
        parked = [
            ws.id
            for ws in self.store.list(Workstream, project_id=self.project.id)
            if ws.status == WorkstreamStatus.parked and question_id in ws.parked_reason
        ]
        if parked:
            return (
                f"withdrawn; workstream(s) {', '.join(parked)} are still parked on it — "
                "reactivate them if work should continue"
            )
        return "withdrawn"

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
        if ws.source == WorkstreamSource.issue:
            return (
                "error: GitHub issue work items are owned by the deterministic "
                "issue pipeline; do not complete them from the build orchestrator."
            )
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

    def create_human_task(
        self,
        title: str,
        instructions_markdown: str,
        kind: str = "external",
        org_wide: bool = False,
        backend: str = "",
        machine: str = "",
    ) -> str:
        """File a todo for the human operator: an action only they can perform
        *outside Hive* — CLI logins on runner machines, DNS records, billing,
        granting access. Never file one for something Hive can do itself
        (cancel_task cancels tasks, withdraw_question retracts questions).
        Unlike ask_user this requests an action, not an answer.

        kind must be 'access' (a login/subscription fix — pass backend and
        machine so the todo carries the exact login recipe and auto-closes when
        the backend probes usable), 'infra' (a machine or capability is offline
        — pass machine; auto-closes on reconnect), or 'external' (DNS, billing,
        anything outside the fleet; the human closes it). Set org_wide=True when
        the action helps all projects, not just this one. One open todo exists
        per condition — a refile for the same condition is rejected."""
        if kind not in (HumanTaskKind.access, HumanTaskKind.infra, HumanTaskKind.external):
            return "error: kind must be one of access, infra, external"
        dedup_key = ""
        resolution: dict = {}
        if kind == HumanTaskKind.access:
            if not backend or not machine:
                return "error: access todos need backend and machine so they can auto-close"
            org_wide = True  # a login serves the whole fleet, not one project
            dedup_key = f"access:{backend}:{machine}"
            resolution = {"check": "resource_usable", "backend": backend, "runner_name": machine}
            hint = REGISTRY[backend].login_hint if backend in REGISTRY else ""
            if hint:
                instructions_markdown = f"{instructions_markdown}\n\n{hint}"
        elif kind == HumanTaskKind.infra and machine:
            dedup_key = f"infra:machine:{machine}"
            resolution = {"check": "machine_online", "machine_name": machine}
        t = escalate(
            self.store,
            title,
            instructions_markdown,
            project_id="" if org_wide else self.project.id,
            workspace_id=self.project.workspace_id,
            kind=HumanTaskKind(kind),
            dedup_key=dedup_key,
            resolution=resolution,
        )
        if t is None:
            return (
                "error: an open todo already covers this condition (see OPEN HUMAN "
                "TODOS in the snapshot); not refiled"
            )
        self.actions.append(f"filed human todo {t.id} '{title}'")
        return f"human_task_id={t.id} (user will see it on the resources page)"

    def mark_goal_complete(self, summary: str) -> str:
        """Declare the iteration goal fully built and verified. Only valid once
        every workstream is done/parked, no tasks are queued or running, and no
        questions are open. The project goes idle until the human sets the next
        goal.

        The summary is the completion note the human reads: it must contain a
        'Try it:' line with the exact command(s) to see the result working
        (e.g. `git clone … && cargo run`), plus the verification evidence
        (test counts, verify verdicts) — a claim without a way to check it is
        not a completion note."""
        unfinished = [
            t
            for t in self.store.list(Task, project_id=self.project.id)
            if t.status in (TaskStatus.pending, TaskStatus.running)
        ]
        active = [
            w
            for w in self.store.list(
                Workstream, project_id=self.project.id, status=WorkstreamStatus.active
            )
            if w.source != WorkstreamSource.issue
        ]
        open_questions = self.store.list(
            Question, project_id=self.project.id, status=QuestionStatus.open
        )
        if unfinished or active or open_questions:
            return (
                f"rejected: {len(unfinished)} unfinished tasks, {len(active)} active "
                f"workstreams, {len(open_questions)} open questions. Finish or park them first."
            )
        # The quality gate is real, not advisory: a workstream counts as built
        # only if its most recent task is a verify that ACCEPTed.
        for ws in self.store.list(Workstream, project_id=self.project.id):
            if ws.source == WorkstreamSource.issue or ws.status != WorkstreamStatus.done:
                continue
            ws_tasks = self.store.list(Task, project_id=self.project.id, workstream_id=ws.id)
            last = ws_tasks[-1] if ws_tasks else None
            if last is None or last.kind != TaskKind.verify or last.verdict != Verdict.accept:
                return (
                    f"rejected: workstream {ws.id} '{ws.title}' is not closed by an accepted "
                    "verify task. Queue a verify task and get an ACCEPT before completing."
                )
        self.project.goal_complete = True
        self.project.goal_complete_note = summary
        self.store.put(self.project)
        self.actions.append("marked goal complete")
        return "goal marked complete"

    # -- context -------------------------------------------------------------

    def snapshot(self) -> str:
        all_workstreams = self.store.list(Workstream, project_id=self.project.id)
        workstreams = list(all_workstreams)
        workstreams = [w for w in workstreams if w.source != WorkstreamSource.issue]
        issue_items = [w for w in all_workstreams if w.source == WorkstreamSource.issue]
        ws_lines = []
        for ws in workstreams:
            desc = ws.description[:200] + ("…" if len(ws.description) > 200 else "")
            line = f"- [{ws.status}] {ws.id} '{ws.title}': {desc}"
            if ws.parked_reason:
                line += f" (parked: {ws.parked_reason})"
            ws_lines.append(line)
        issue_lines = []
        for ws in sorted(issue_items, key=lambda w: (w.order, w.issue_number))[-20:]:
            line = f"- [{ws.status}] issue #{ws.issue_number} {ws.id} '{ws.title}'"
            if ws.parked_reason:
                line += f" (note: {ws.parked_reason})"
            issue_lines.append(line)
        task_lines = []
        for t in self.store.list(Task, project_id=self.project.id, limit=15):
            line = f"- [{t.status}] {t.kind} task {t.id} ws={t.workstream_id} repo={t.repo} backend={t.backend}"
            if t.branch:
                line += f" branch={t.branch}"
            if t.kind == TaskKind.verify and t.verdict != Verdict.none:
                line += f" verdict={t.verdict}"
            if t.status in (TaskStatus.done, TaskStatus.failed, TaskStatus.cancelled):
                line += f"\n  result: {t.result_text[:RESULT_SNIPPET]}"
            task_lines.append(line)
        q_lines = [
            f"- [{q.status}] {q.id}: {q.text[:500]}" + (f"\n  answer: {q.answer}" if q.answer else "")
            for q in self.store.list(Question, project_id=self.project.id, limit=10)
        ]
        runner_lines = [
            f"- {r.name}: backends={','.join(r.backends)} {'online' if r.online() else 'OFFLINE'}"
            for r in self.store.list(Runner, workspace_id=self.project.workspace_id)
        ]
        runners_by_id = {
            r.id: r for r in self.store.list(Runner, workspace_id=self.project.workspace_id)
        }
        resource_lines = []
        for res in self.store.list(Resource, workspace_id=self.project.workspace_id):
            runner = runners_by_id.get(res.runner_id)
            runner_name = runner.name if runner else res.runner_id
            cooldown = f", cooldown_until={res.cooldown_until:.0f}" if res.cooldown_until else ""
            available = (
                res.available()
                and runner is not None
                and runner.online()
                and res.backend in runner.backends
            )
            discovery = (
                f", discovery={res.discovery_status}"
                if res.discovery_status != "unknown"
                else ""
            )
            resource_lines.append(
                f"- {runner_name}/{res.backend}: usability={res.usability_status}, "
                f"available={available}{cooldown}{discovery}"
            )
        todo_lines = [
            f"- {t.id} [{'org-wide' if not t.project_id else 'this project'}] ({t.kind}): {t.title}"
            f"{' — closes itself when resolved' if t.resolution else ''}"
            for t in self.store.list(
                HumanTask, workspace_id=self.project.workspace_id, status=HumanTaskStatus.open
            )
            if t.project_id in ("", self.project.id)
        ]
        sub_lines = [
            f"- {s.provider} ({s.plan or 'plan?'}): {s.notes}"
            for s in self.store.list(Subscription, workspace_id=self.project.workspace_id)
        ]
        feedback_lines = [
            f"- {f.verdict} on {f.target_id}: {f.comment}"
            for f in self.store.list(Feedback, project_id=self.project.id, limit=5)
        ]
        p = self.project
        return "\n".join(
            [
                f"PROJECT {p.name} | mode={p.mode} "
                f"autonomy={p.autonomy} guess_propensity={p.guess_propensity} "
                f"goal_complete={p.goal_complete}",
                f"member repos: {', '.join(p.member_repos) or '(none)'}",
                f"spec repo: {p.spec_repo}",
                "",
                "WORK ITEMS:",
                *(ws_lines or ["(none yet)"]),
                "",
                "GITHUB ISSUE WORK ITEMS (deterministic pipeline, read-only to planner):",
                *(issue_lines or ["(none)"]),
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
                "BACKEND RESOURCES:",
                *(resource_lines or ["(none registered — tasks will wait)"]),
                "",
                "OPEN HUMAN TODOS (yours + org-wide):",
                *(todo_lines or ["(none)"]),
                "",
                "SUBSCRIPTIONS (capacity that may exist beyond advertised runners):",
                *(sub_lines or ["(none)"]),
                "",
                "RECENT FEEDBACK (human verdicts on tasks/questions):",
                *(feedback_lines or ["(none)"]),
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

        tools = Tools(self.store, project, spec, self.config.gh_token)
        event_text = "\n".join(f"- {e}" for e in events)
        user_msg = (
            f"EVENTS:\n{event_text}\n\nSTATE SNAPSHOT:\n{tools.snapshot()}"
            f"\n\nSPEC:\n{spec_digest}"
        )
        history = self._load_history(project_id)
        result = self._generate(project, history, user_msg, tools)
        final_text = result.text
        self._record_cost(project, result)
        log.info("orchestrator[%s]: %s | actions: %s", project.name, final_text[:200], tools.actions)

        # Persist model text only — echoing executed tool calls as text teaches
        # the model to narrate actions instead of calling tools. The snapshot in
        # the next invocation is the ground truth for what actually happened.
        history.append({"role": "user", "text": f"EVENTS:\n{event_text}"})
        history.append({"role": "model", "text": final_text})
        self._save_history(project_id, history[-HISTORY_LIMIT:])

    # -- LLM call -------------------------------------------------------------

    def _generate(
        self, project: Project, history: list[dict], user_msg: str, tools: Tools
    ) -> LoopResult:
        system = self._system_prompt(project)
        toolset = ToolSet(tools.functions())
        adapters = self._build_adapters()
        last_unavailable: ProviderUnavailable | None = None
        for adapter in adapters:
            try:
                result = ToolLoop(MAX_REMOTE_CALLS).run(adapter, system, history, user_msg, toolset)
            except ProviderUnavailable as exc:
                # Falling back is only safe before any tool ran: a provider that
                # died mid-loop may have already created workstreams/tasks, and
                # restarting on another provider would duplicate them.
                if tools.actions:
                    raise
                model = getattr(adapter, "model", "") or type(adapter).__name__
                log.warning("orchestrator provider %s unavailable: %s — trying next", model, exc)
                last_unavailable = exc
                continue
            result.model = getattr(adapter, "model", "")
            return result
        raise last_unavailable  # only reached when every provider was unavailable

    def _build_adapters(self):
        """The provider seam: tests override this to inject scripted adapters.
        A list, tried in order — the orchestrator falls back when one is out of
        quota or otherwise unavailable."""
        return build_adapters(self.config)

    def _record_cost(self, project: Project, result: LoopResult) -> None:
        cost = estimate_cost(result.model, result.usage.input_tokens, result.usage.output_tokens)
        self.store.put(
            OrchestratorRun(
                workspace_id=project.workspace_id,
                project_id=project.id,
                model=result.model,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=cost,
            )
        )

    def _system_prompt(self, project: Project) -> str:
        base_prompt, _version = load_prompt("orchestrator")
        org_context = self.store.get_org_context(project.workspace_id)
        return base_prompt + (f"\n\nORG CONTEXT:\n{org_context}" if org_context else "")

    # -- history persistence ---------------------------------------------------

    def _history_blob(self, project_id: str) -> str:
        project = self.store.get(Project, project_id)
        workspace_id = project.workspace_id if project else "unknown"
        return f"workspaces/{workspace_id}/orchestrator-context/{project_id}.json"

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
