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
from pathlib import Path

from hive.agents import REGISTRY
from hive._control import allowances
from hive._control.escalation import escalate
from hive._workstreams import plans
from hive.llm import LoopResult, ProviderUnavailable, ToolLoop, ToolSet, build_adapters
from hive.models import (
    Feedback,
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    OrchestratorRun,
    PlanStatus,
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
)
from hive.llm._pricing import estimate_cost
from hive.llm.prompts import load as load_prompt
from hive._integrations.specrepo import SpecRepo

log = logging.getLogger("hive._control.orchestrator")

HISTORY_LIMIT = 80
RESULT_SNIPPET = 4000
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
            self.propose_plan,
            self.amend_plan,
            self.ask_user,
            self.withdraw_question,
            self.commit_to_spec,
            self.create_human_task,
            self.mark_goal_complete,
        ]

    # -- tools ---------------------------------------------------------------

    def _parse_items(self, items_json: str) -> list[dict] | str:
        try:
            items = json.loads(items_json)
        except json.JSONDecodeError as exc:
            return f"error: items_json is not valid JSON: {exc}"
        if not isinstance(items, list) or not items:
            return "error: items_json must be a non-empty JSON list of item objects"
        if not all(isinstance(i, dict) and str(i.get("title") or "").strip() for i in items):
            return "error: every item must be an object with at least a non-empty 'title'"
        return items

    def propose_plan(self, goal: str, items_json: str) -> str:
        """Draft the iteration plan for the human to review. items_json is a
        JSON list of item objects, in execution order:
        {"title", "story", "constraints", "notes", "repo"?} — title is the
        high-level statement ('add mobile support'); story says who can do
        what once it lands; constraints are sparse technical boundaries, not a
        blueprint; repo only when it differs from the spec repo. Nothing
        executes until the human approves the plan, and the human may rewrite
        any part of it — write items that read well standalone. Replaces any
        unapproved draft."""
        items = self._parse_items(items_json)
        if isinstance(items, str):
            return items
        try:
            plan = plans.create_draft(self.store, self.project, goal, items, proposed_by="agent")
        except ValueError as exc:
            return f"error: {exc}"
        self.actions.append(f"proposed plan {plan.id} with {len(items)} item(s)")
        return f"plan_id={plan.id} drafted with {len(items)} item(s); awaiting the human's review"

    def amend_plan(self, items_json: str) -> str:
        """Propose additional items for the live plan (same JSON shape as
        propose_plan). Use when landed work revealed necessary follow-ups; the
        items enter as proposals the human must approve before they execute.
        Never re-add a rejected item without a changed approach, and never
        amend around a blocked item — that is the human's call."""
        plan = plans.active_plan(self.store, self.project)
        if plan is None:
            return "error: no live plan; use propose_plan"
        items = self._parse_items(items_json)
        if isinstance(items, str):
            return items
        try:
            added = [plans.add_item(self.store, self.project, plan, i, "agent") for i in items]
        except ValueError as exc:
            return f"error: {exc}"
        self.actions.append(f"proposed {len(added)} amendment item(s) on plan {plan.id}")
        return f"{len(added)} item(s) proposed; they execute only after the human approves them"

    def ask_user(self, question_markdown: str) -> str:
        """Ask the human a clarification question (markdown: context, the
        gap/contradiction, options, your recommendation). Reserve it for
        decisions that change what gets built; batch related questions."""
        if problem := _structured_question_problem(question_markdown):
            return problem
        q = self.store.put(
            Question(
                workspace_id=self.project.workspace_id,
                project_id=self.project.id,
                text=question_markdown,
            )
        )
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
        return "withdrawn"

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
        """Declare the iteration goal fully built. Only valid once the
        iteration plan is complete (every item merged, or cancelled by the
        human), nothing is queued or running, and no questions are open. The
        project goes idle until the human sets the next goal.

        The summary is the completion note the human reads: it must contain a
        'Try it:' line with the exact command(s) to see the result working
        (e.g. `git clone … && cargo run`), plus the verification evidence
        (review verdicts, test counts) — a claim without a way to check it is
        not a completion note."""
        # The quality gate is structural: every plan item landed only through
        # an accepted fresh-agent review, so a complete plan IS the evidence.
        plan = plans.latest_plan(self.store, self.project)
        if plan is None:
            return (
                "rejected: no iteration plan exists. The goal completes only through "
                "an approved plan landing; propose_plan first."
            )
        if plan.status != PlanStatus.complete:
            return (
                f"rejected: the iteration plan is {plan.status}. Every item must merge "
                "(or be cancelled by the human) before the goal can complete."
            )
        unfinished = [
            t
            for t in self.store.list(Task, project_id=self.project.id)
            if t.status in (TaskStatus.pending, TaskStatus.running)
        ]
        open_questions = self.store.list(
            Question, project_id=self.project.id, status=QuestionStatus.open
        )
        if unfinished or open_questions:
            return (
                f"rejected: {len(unfinished)} unfinished tasks, "
                f"{len(open_questions)} open questions. Finish them or withdraw moot questions first."
            )
        self.project.goal_complete = True
        self.project.goal_complete_note = summary
        self.store.put(self.project)
        self.actions.append("marked goal complete")
        return "goal marked complete"

    # -- context -------------------------------------------------------------

    def snapshot(self) -> str:
        plan = plans.latest_plan(self.store, self.project)
        if plan is None:
            plan_lines = ["(no plan — propose_plan when the spec warrants work)"]
        else:
            plan_lines = [f"[{plan.status}] plan {plan.id} goal: {plan.goal[:300]}"]
            for item in plans.plan_items(self.store, plan):
                line = f"- [{item.status}] {item.order + 1}. '{item.title}' ({item.id})"
                if item.story:
                    line += f" story: {item.story[:150]}"
                if item.parked_reason:
                    line += f"\n  parked: {item.parked_reason[:400]}"
                plan_lines.append(line)
        issue_items = [
            w
            for w in self.store.list(Workstream, project_id=self.project.id)
            if w.source == WorkstreamSource.issue
        ]
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
        allowance_left = allowances.remaining(
            p.agent_grants,
            allowances.sessions_today(
                self.store.list(Task, project_id=p.id), allowances.utc_day_start()
            ),
        )
        return "\n".join(
            [
                f"PROJECT {p.name} | mode={p.mode} "
                f"autonomy={p.autonomy} guess_propensity={p.guess_propensity} "
                f"goal_complete={p.goal_complete}",
                f"member repos: {', '.join(p.member_repos) or '(none)'}",
                f"spec repo: {p.spec_repo}",
                "AGENT ALLOWANCE (sessions/day; disallowed tasks cannot dispatch): "
                + allowances.describe(p.agent_grants, allowance_left),
                "",
                "ITERATION PLAN (executes via the deterministic pipeline once the human approves):",
                *plan_lines,
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
