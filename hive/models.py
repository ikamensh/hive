"""Domain models. All persisted via Store as plain dicts (pydantic round-trip)."""

from __future__ import annotations

import time
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


DEFAULT_WORKSPACE_ID = "default"


class User(BaseModel):
    id: str = Field(default_factory=new_id)
    github_login: str = ""
    display_name: str = ""
    github_access_token: str = ""  # OAuth token for GitHub API; never exposed via web API
    created_at: float = Field(default_factory=now)
    last_seen: float = Field(default_factory=now)


class Workspace(BaseModel):
    id: str = DEFAULT_WORKSPACE_ID
    name: str = "personal"
    created_at: float = Field(default_factory=now)


class WorkspaceMembership(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    user_id: str
    role: str = "owner"
    created_at: float = Field(default_factory=now)


class Machine(BaseModel):
    """A durable machine the user recognizes. Runner/control-plane processes
    are ephemeral; this record is what keeps offline machines visible."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    name: str
    hostname: str = ""
    kind: str = "unknown"  # process role: control-plane | runner | unknown
    machine_type: str = ""  # human-facing host type: macbook | linux | win | ...
    os: str = ""
    arch: str = ""
    device_kind: str = "unknown"  # availability class: laptop | server | unknown
    first_seen: float = Field(default_factory=now)
    last_seen: float = Field(default_factory=now)


class Mode(StrEnum):
    build = "build"
    maintain = "maintain"


class WorkSource(StrEnum):
    """Where a project's work comes from. `spec` = the human sets an iteration
    goal that the orchestrator decomposes into workstreams (the default flow).
    `issues` = the orchestrator works the spec repo's open GitHub issues, one
    at a time, in a planned sequence."""

    spec = "spec"
    issues = "issues"


class Autonomy(StrEnum):
    pr = "pr"
    direct_push = "direct_push"


class GuessPropensity(StrEnum):
    never = "never"
    rarely = "rarely"
    sometimes = "sometimes"
    often = "often"
    always = "always"


class ProjectState(StrEnum):
    intake = "intake"  # spec mode: intake scout is aligning the project before planning
    working = "working"
    blocked_questions = "blocked_questions"
    blocked_resources = "blocked_resources"
    blocked_budget = "blocked_budget"  # daily soft cap reached; resets at UTC midnight
    blocked_clarity = "blocked_clarity"  # issues mode: open issues stuck on a human (blocked/rejected)
    idle_goal_complete = "idle_goal_complete"
    idle_no_workstreams = "idle_no_workstreams"
    idle_no_open_issues = "idle_no_open_issues"  # issues mode: queue drained


class Project(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    name: str
    spec_repo: str = ""  # git URL of the spec home; empty = draft (not yet configured)
    member_repos: list[str] = []  # git URLs; spec_repo included if it holds code
    mode: Mode = Mode.build
    work_source: WorkSource = WorkSource.spec
    autonomy: Autonomy = Autonomy.direct_push
    guess_propensity: GuessPropensity = GuessPropensity.sometimes
    prod_deploys: bool = False
    paused: bool = False
    daily_budget_usd: float = 0.0  # 0 = no cap; else soft cap on today's task spend
    goal_complete: bool = False
    goal_complete_note: str = ""
    intake_conversation_id: str = ""
    state: ProjectState = ProjectState.idle_no_workstreams  # cached by supervisor
    created_at: float = Field(default_factory=now)


class ConversationRole(StrEnum):
    intake = "intake"


class ConversationStatus(StrEnum):
    open = "open"  # ready for a user message / approval, or no turn queued yet
    running = "running"  # an agent turn is pending/running
    finalizing = "finalizing"  # approved; scout is committing/pushing durable spec
    done = "done"
    failed = "failed"


class AgentConversation(BaseModel):
    """A durable multi-turn agent thread. `Task` remains the execution ledger;
    a conversation owns continuity across those task turns."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    role: ConversationRole = ConversationRole.intake
    repo: str
    backend: str
    model: str = ""
    status: ConversationStatus = ConversationStatus.open
    session_handle: str = ""  # backend resume id when available
    latest_brief: str = ""
    transcript: list[dict[str, str]] = []  # compact fallback when true resume is unavailable
    last_task_id: str = ""
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


class WorkstreamStatus(StrEnum):
    active = "active"
    queued = "queued"  # issues mode: ingested, awaiting its turn (strict one-at-a-time)
    parked = "parked"
    done = "done"
    # issues-mode per-issue pipeline (see wiki/issues-mode.md):
    resolving = "resolving"  # resolve task (clarify→fix) in flight
    blocked_clarity = "blocked_clarity"  # resolve returned BLOCKED; agent commented on the issue
    reviewing = "reviewing"  # review task in flight
    rejected = "rejected"  # review returned REJECT; agent commented with the failure + next approach
    cancelled = "cancelled"  # backing issue closed on GitHub by a human


# Issue-workstream states the human must act on before Hive can make progress.
ISSUE_BLOCKED = (WorkstreamStatus.blocked_clarity, WorkstreamStatus.rejected)


class WorkstreamSource(StrEnum):
    manual = "manual"  # decomposed from the iteration goal by the orchestrator
    issue = "issue"  # ingested from a GitHub issue (issues mode)


class Workstream(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    title: str
    description: str = ""
    status: WorkstreamStatus = WorkstreamStatus.active
    parked_reason: str = ""
    source: WorkstreamSource = WorkstreamSource.manual
    issue_number: int = 0  # GitHub issue number when source=issue
    issue_url: str = ""
    issue_attachments: list[str] = []  # embedded image URLs from the issue + comments
    order: int = 0  # planned position in the issue queue (lower = sooner; dormant ordering variant)
    created_at: float = Field(default_factory=now)


class TaskStatus(StrEnum):
    pending = "pending"
    running = "running"  # dispatched to a runner
    done = "done"
    failed = "failed"  # runner-level failure (timeout, crash, resource exhausted)
    cancelled = "cancelled"  # stopped by the operator before completing


class TaskKind(StrEnum):
    work = "work"
    verify = "verify"
    probe = "probe"
    intake = "intake"
    resolve = "resolve"  # issues mode: one codex session clarifies then (if clear) fixes
    review = "review"  # issues mode: fresh agent reviews the fix, may fix on the spot
    preflight = "preflight"  # issues mode: runner self-check (git push + gh auth) before a big run


class Verdict(StrEnum):
    none = "none"  # not a verify task, or no parseable verdict
    accept = "accept"
    reject = "reject"


def parse_verdict(text: str) -> Verdict:
    """Extract a verify agent's verdict from its result text. The verifier
    prompt requires a `VERDICT: ACCEPT|REJECT` line; we read the last one so a
    quoted instruction earlier in the report can't spoof the outcome."""
    found = Verdict.none
    for line in text.splitlines():
        token = line.strip().upper()
        if token.startswith("VERDICT:") and "ACCEPT" in token:
            found = Verdict.accept
        elif token.startswith("VERDICT:") and "REJECT" in token:
            found = Verdict.reject
    return found


def parse_resolve(text: str) -> Verdict:
    """Extract a resolve task's outcome (accept = FIXED → go to review, reject =
    BLOCKED → the agent made no change and commented, because the issue was
    underspecified or the bug could not be reproduced on the working branch).
    Requires an `OUTCOME: FIXED|BLOCKED` line; the last one wins so quoted text
    can't spoof."""
    found = Verdict.none
    for line in text.splitlines():
        token = line.strip().upper()
        if token.startswith("OUTCOME:") and "FIX" in token:
            found = Verdict.accept
        elif token.startswith("OUTCOME:") and "BLOCK" in token:
            found = Verdict.reject
    return found


def parse_review(text: str) -> Verdict:
    """Extract a review task's verdict (accept = ACCEPT → merge+close, reject =
    REJECT → the agent commented with the failure). Requires a `REVIEW:
    ACCEPT|REJECT` line; the last one wins."""
    found = Verdict.none
    for line in text.splitlines():
        token = line.strip().upper()
        if token.startswith("REVIEW:") and "ACCEPT" in token:
            found = Verdict.accept
        elif token.startswith("REVIEW:") and "REJECT" in token:
            found = Verdict.reject
    return found


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str
    repo: str  # git URL the runner checks out
    branch: str = ""  # non-default branch to check out (PR-mode work and its verify/fix)
    fresh_branch: bool = False  # reset an existing task branch to default before running
    kind: TaskKind = TaskKind.work
    instructions: str
    conversation_id: str = ""
    conversation_turn: str = ""  # intake: initial | message | proceed | finalize
    session_handle: str = ""  # runner resumes this backend session when possible
    issue_number: int = 0  # issues mode: the issue this task resolves/reviews
    issue_doc: str = ""  # issues mode: full issue markdown (title+body+comments) → .hive ISSUE.md
    issue_attachments: list[str] = []  # issues mode: image filenames the runner fetches from the control plane
    backend: str = "cursor"  # kodo backend name: claude | cursor | codex | gemini-cli
    model: str = ""  # backend default when empty
    status: TaskStatus = TaskStatus.pending
    runner_id: str = ""
    delivered: bool = False  # runner has picked the assignment up via poll
    cancel_requested: bool = False  # operator asked to stop; runner honors cooperatively
    verdict: Verdict = Verdict.none  # parsed from a verify task's result
    trace_blob: str = ""  # blob key of the kodo JSONL run trace, once uploaded
    result_text: str = ""
    is_error: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_versions: dict[str, str] = {}  # role -> content hash
    created_at: float = Field(default_factory=now)
    started_at: float = 0.0
    finished_at: float = 0.0


class QuestionStatus(StrEnum):
    open = "open"
    answered = "answered"
    dismissed = "dismissed"  # operator discarded it without answering


class Question(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str = ""  # empty = project-level
    text: str  # markdown: context, the gap, options, recommendation
    status: QuestionStatus = QuestionStatus.open
    answer: str = ""
    created_at: float = Field(default_factory=now)
    answered_at: float = 0.0


class Runner(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    machine_id: str = ""
    name: str
    backends: list[str] = []  # installed agent CLIs
    last_seen: float = Field(default_factory=now)

    ONLINE_WINDOW_S: float = 90.0

    def online(self) -> bool:
        return now() - self.last_seen < self.ONLINE_WINDOW_S


class ResourceUsability(StrEnum):
    unknown = "unknown"  # detected but never proven by a probe
    probing = "probing"
    usable = "usable"
    failed = "failed"


class Resource(BaseModel):
    """One (runner, backend) capacity unit with observed-usage accounting."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    machine_id: str = ""
    runner_id: str
    backend: str
    discovery_status: str = "unknown"  # runner-local CLI check: missing | ok | warning | error
    discovery_text: str = ""
    discovered_at: float = 0.0
    cli_path: str = ""
    cli_version: str = ""
    usability_status: ResourceUsability = ResourceUsability.unknown
    last_probe_at: float = 0.0
    last_probe_task_id: str = ""
    last_probe_text: str = ""
    cooldown_until: float = 0.0  # epoch; >now means exhausted
    last_exhaustion_at: float = 0.0
    last_exhaustion_text: str = ""  # runner-reported quota/rate-limit message
    last_exhaustion_task_id: str = ""
    total_cost_usd: float = 0.0
    total_tasks: int = 0
    enabled: bool = True
    disabled_reason: str = ""

    def available(self) -> bool:
        return (
            self.enabled
            and self.usability_status == ResourceUsability.usable
            and now() >= self.cooldown_until
        )

    def mark_exhausted(self, *, until: float, at: float, text: str, task_id: str) -> None:
        self.cooldown_until = until
        self.last_exhaustion_at = at
        self.last_exhaustion_text = text[:2000]
        self.last_exhaustion_task_id = task_id

    def clear_exhaustion(self) -> None:
        self.cooldown_until = 0.0
        self.last_exhaustion_at = 0.0
        self.last_exhaustion_text = ""
        self.last_exhaustion_task_id = ""


class Subscription(BaseModel):
    """An AI subscription the user owns (Claude Max, ChatGPT/codex, Cursor...).
    Informs orchestration about capacity that exists beyond what runners
    currently advertise, and anchors login-todos for remote nodes."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    provider: str  # backend name it powers: claude | codex | cursor | gemini-cli
    plan: str = ""  # e.g. "ChatGPT Plus", "Claude Max 5x"
    notes: str = ""  # e.g. which machines are logged in, renewal dates
    created_at: float = Field(default_factory=now)


class HumanTaskStatus(StrEnum):
    open = "open"
    done = "done"


class HumanTask(BaseModel):
    """A todo for the human operator (auth refresh, infra unblock, ...) with
    concrete instructions. Surfaced in the web UI next to questions."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str = ""  # empty = org-wide (runner logins, billing, DNS)
    title: str
    instructions: str  # markdown, copy-pasteable commands
    status: HumanTaskStatus = HumanTaskStatus.open
    created_at: float = Field(default_factory=now)
    done_at: float = 0.0


class Feedback(BaseModel):
    """Explicit human feedback on a task or question. Future GEPA input."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    target_id: str  # task or question id
    verdict: str  # "up" | "down"
    comment: str = ""
    created_at: float = Field(default_factory=now)


class OrchestratorRun(BaseModel):
    """One orchestrator invocation's LLM usage. Recorded so the planner's own
    spend is visible and counts against the project budget (runner task cost is
    tracked on Task; this is the control-plane side of the bill)."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    created_at: float = Field(default_factory=now)
