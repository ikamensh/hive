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
    working = "working"
    blocked_questions = "blocked_questions"
    blocked_resources = "blocked_resources"
    blocked_budget = "blocked_budget"  # daily soft cap reached; resets at UTC midnight
    idle_goal_complete = "idle_goal_complete"
    idle_no_workstreams = "idle_no_workstreams"


class Project(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    name: str
    spec_repo: str = ""  # git URL of the spec home; empty = draft (not yet configured)
    member_repos: list[str] = []  # git URLs; spec_repo included if it holds code
    mode: Mode = Mode.build
    autonomy: Autonomy = Autonomy.direct_push
    guess_propensity: GuessPropensity = GuessPropensity.sometimes
    prod_deploys: bool = False
    paused: bool = False
    daily_budget_usd: float = 0.0  # 0 = no cap; else soft cap on today's task spend
    goal_complete: bool = False
    goal_complete_note: str = ""
    state: ProjectState = ProjectState.idle_no_workstreams  # cached by supervisor
    created_at: float = Field(default_factory=now)


class WorkstreamStatus(StrEnum):
    active = "active"
    parked = "parked"
    done = "done"


class Workstream(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    title: str
    description: str = ""
    status: WorkstreamStatus = WorkstreamStatus.active
    parked_reason: str = ""
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


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str
    repo: str  # git URL the runner checks out
    branch: str = ""  # non-default branch to check out (PR-mode work and its verify/fix)
    kind: TaskKind = TaskKind.work
    instructions: str
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
