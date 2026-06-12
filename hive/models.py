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
    idle_goal_complete = "idle_goal_complete"
    idle_no_workstreams = "idle_no_workstreams"


class Project(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    spec_repo: str  # git URL of the spec home; may equal a member repo
    member_repos: list[str] = []  # git URLs; spec_repo included if it holds code
    mode: Mode = Mode.build
    autonomy: Autonomy = Autonomy.direct_push
    guess_propensity: GuessPropensity = GuessPropensity.sometimes
    prod_deploys: bool = False
    paused: bool = False
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


class TaskKind(StrEnum):
    work = "work"
    verify = "verify"


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    workstream_id: str
    repo: str  # git URL the runner checks out
    kind: TaskKind = TaskKind.work
    instructions: str
    backend: str = "cursor"  # kodo backend name: claude | cursor | codex | gemini-cli
    model: str = ""  # backend default when empty
    status: TaskStatus = TaskStatus.pending
    runner_id: str = ""
    delivered: bool = False  # runner has picked the assignment up via poll
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


class Question(BaseModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    workstream_id: str = ""  # empty = project-level
    text: str  # markdown: context, the gap, options, recommendation
    status: QuestionStatus = QuestionStatus.open
    answer: str = ""
    created_at: float = Field(default_factory=now)
    answered_at: float = 0.0


class Runner(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    backends: list[str] = []  # installed agent CLIs
    last_seen: float = Field(default_factory=now)

    ONLINE_WINDOW_S: float = 90.0

    def online(self) -> bool:
        return now() - self.last_seen < self.ONLINE_WINDOW_S


class Resource(BaseModel):
    """One (runner, backend) capacity unit with observed-usage accounting."""

    id: str = Field(default_factory=new_id)
    runner_id: str
    backend: str
    cooldown_until: float = 0.0  # epoch; >now means exhausted
    total_cost_usd: float = 0.0
    total_tasks: int = 0

    def available(self) -> bool:
        return now() >= self.cooldown_until


class Subscription(BaseModel):
    """An AI subscription the user owns (Claude Max, ChatGPT/codex, Cursor...).
    Informs orchestration about capacity that exists beyond what runners
    currently advertise, and anchors login-todos for remote nodes."""

    id: str = Field(default_factory=new_id)
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
    title: str
    instructions: str  # markdown, copy-pasteable commands
    status: HumanTaskStatus = HumanTaskStatus.open
    created_at: float = Field(default_factory=now)
    done_at: float = 0.0


class Feedback(BaseModel):
    """Explicit human feedback on a task or question. Future GEPA input."""

    id: str = Field(default_factory=new_id)
    project_id: str
    target_id: str  # task or question id
    verdict: str  # "up" | "down"
    comment: str = ""
    created_at: float = Field(default_factory=now)
