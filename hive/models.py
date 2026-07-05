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
    """A durable machine the user recognizes. Runner/chief processes
    are ephemeral; this record is what keeps offline machines visible."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    name: str
    hostname: str = ""
    kind: str = "unknown"  # process role: chief | runner | unknown
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
    intake = "intake"  # intake scout is aligning the project before planning
    working = "working"
    needs_attention = "needs_attention"
    blocked_questions = "blocked_questions"
    blocked_resources = "blocked_resources"
    blocked_budget = "blocked_budget"  # daily soft cap reached; resets at UTC midnight
    blocked_clarity = "blocked_clarity"
    idle_goal_complete = "idle_goal_complete"
    idle = "idle"
    idle_no_workstreams = "idle_no_workstreams"


class Project(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    name: str
    spec_repo: str = ""  # git URL of the spec home; empty = draft (not yet configured)
    # The spec the user handed over at creation, verbatim. Intake treats it as the
    # primary statement of intent and preserves it under input-log/ when finalizing.
    initial_spec: str = ""
    member_repos: list[str] = []  # git URLs; spec_repo included if it holds code
    mode: Mode = Mode.build
    autonomy: Autonomy = Autonomy.direct_push
    guess_propensity: GuessPropensity = GuessPropensity.sometimes
    prod_deploys: bool = False
    ci_autofix: bool = False  # poll each repo's default-branch CI; file+fix an issue when red
    # Autonomous testing: Hive keeps the story backlog aligned (auto refresh when
    # missing/weak) and sweeps unproven stories (auto episodes) on its own. Only
    # acts inside an explicit budget envelope (daily_budget_usd > 0).
    testing_auto: bool = True
    paused: bool = False
    archived: bool = False  # hidden from the default list; data retained
    # One number for money: the daily soft cap on *all* the project's paid work —
    # planner invocations, build/verify tasks, and autonomous testing alike.
    # 0 pauses paid work entirely (blocked_budget); raise it to spend more.
    daily_budget_usd: float = 10.0
    goal_complete: bool = False
    goal_complete_note: str = ""
    intake_conversation_id: str = ""
    state: ProjectState = ProjectState.idle  # cached by supervisor
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


class ProjectWorkstreamKind(StrEnum):
    iteration = "iteration"
    github_issues = "github_issues"
    testing = "testing"


class ProjectWorkstreamStatus(StrEnum):
    idle = "idle"
    active = "active"
    blocked = "blocked"
    disabled = "disabled"


class ProjectWorkstream(BaseModel):
    """An ongoing channel of project work.

    The current `Workstream` model below is still the smaller work-item record
    during migration. This model is the target workstream layer from
    wiki/unified-project-work.md.
    """

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    kind: ProjectWorkstreamKind
    title: str
    repo: str = ""
    source_ref: dict = {}
    status: ProjectWorkstreamStatus = ProjectWorkstreamStatus.idle
    enabled: bool = True
    config: dict = {}
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


class WorkstreamStatus(StrEnum):
    active = "active"
    queued = "queued"  # issue solving: ingested, awaiting its turn (strict one-at-a-time)
    parked = "parked"
    done = "done"
    # issue-solving per-issue pipeline (see wiki/issue-solving.md):
    resolving = "resolving"  # resolve task (clarify→fix) in flight
    blocked_clarity = "blocked_clarity"  # resolve returned BLOCKED; agent commented on the issue
    reviewing = "reviewing"  # review task in flight
    rejected = "rejected"  # review returned REJECT; agent commented with the failure + next approach
    cancelled = "cancelled"  # backing issue closed on GitHub by a human


# Issue-workstream states the human must act on before Hive can make progress.
ISSUE_BLOCKED = (WorkstreamStatus.blocked_clarity, WorkstreamStatus.rejected)


class WorkstreamSource(StrEnum):
    manual = "manual"  # decomposed from the iteration goal by the orchestrator
    issue = "issue"  # ingested from a GitHub issue


class Workstream(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str = ""  # target parent ProjectWorkstream id; empty for legacy rows
    repo: str = ""
    title: str
    description: str = ""
    status: WorkstreamStatus = WorkstreamStatus.active
    parked_reason: str = ""
    source: WorkstreamSource = WorkstreamSource.manual
    issue_number: int = 0  # GitHub issue number when source=issue
    issue_url: str = ""
    issue_attachments: list[str] = []  # embedded image URLs from the issue + comments
    external_ref: dict = {}
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
    resolve = "resolve"  # issue solving: one codex session clarifies then (if clear) fixes
    review = "review"  # issue solving: fresh agent reviews the fix, may fix on the spot
    preflight = "preflight"  # issue solving: runner self-check (git push + gh auth) before a big run
    test_refresh = "test_refresh"  # testing: refresh/reconcile acceptance stories in the spec home
    test_sweep = "test_sweep"  # testing: exploratory black-box sweep for one story
    test_reproduce = "test_reproduce"  # testing: independent bug reproduction
    test_judge = "test_judge"  # testing: UX-smell adjudication


class TestSweepOutcome(StrEnum):
    none = "none"
    passed = "pass"
    findings = "findings"
    blocked = "blocked"


class TestReproOutcome(StrEnum):
    none = "none"
    confirmed = "confirmed"
    not_reproduced = "not_reproduced"


class TestUxOutcome(StrEnum):
    none = "none"
    improvable = "improvable"
    constrained = "constrained"
    disagree = "disagree"


class Verdict(StrEnum):
    none = "none"  # not a verify task, or no parseable verdict
    accept = "accept"
    reject = "reject"


def _last_marker[T](text: str, prefix: str, options: dict[str, T], default: T) -> T:
    """Read the agent's outcome off a `PREFIX: VALUE` line. The last matching
    line wins, so a quoted instruction earlier in the report can't spoof the
    outcome; within one line, `options` order is precedence (first keyword found
    decides). A `PREFIX:` line whose value matches nothing leaves the prior
    result unchanged. All matching is case-insensitive."""
    head = prefix.upper() + ":"
    found = default
    for line in text.splitlines():
        token = line.strip().upper()
        if not token.startswith(head):
            continue
        for keyword, value in options.items():
            if keyword in token:
                found = value
                break
    return found


def parse_verdict(text: str) -> Verdict:
    """A verify agent's `VERDICT: ACCEPT|REJECT`."""
    return _last_marker(text, "VERDICT", {"ACCEPT": Verdict.accept, "REJECT": Verdict.reject}, Verdict.none)


def parse_resolve(text: str) -> Verdict:
    """A resolve task's `OUTCOME: FIXED|BLOCKED` — FIXED → review, BLOCKED → the
    agent made no change and commented (issue underspecified or unreproducible)."""
    return _last_marker(text, "OUTCOME", {"FIX": Verdict.accept, "BLOCK": Verdict.reject}, Verdict.none)


def parse_review(text: str) -> Verdict:
    """A review task's `REVIEW: ACCEPT|REJECT` — ACCEPT → merge+close."""
    return _last_marker(text, "REVIEW", {"ACCEPT": Verdict.accept, "REJECT": Verdict.reject}, Verdict.none)


def parse_test_refresh(text: str) -> bool:
    """True when a test-refresh task reports `REFRESH: DONE`."""
    return _last_marker(text, "REFRESH", {"DONE": True}, False)


def parse_test_sweep(text: str) -> TestSweepOutcome:
    """A sweep's `SWEEP: PASS|FINDINGS|BLOCKED`."""
    return _last_marker(
        text,
        "SWEEP",
        {
            "FINDINGS": TestSweepOutcome.findings,
            "BLOCK": TestSweepOutcome.blocked,
            "PASS": TestSweepOutcome.passed,
        },
        TestSweepOutcome.none,
    )


def parse_test_repro(text: str) -> TestReproOutcome:
    """A bug-reproduction `REPRO: CONFIRMED|NOT_REPRODUCED`."""
    return _last_marker(
        text,
        "REPRO",
        {
            "NOT_REPRODUCED": TestReproOutcome.not_reproduced,
            "NOT REPRODUCED": TestReproOutcome.not_reproduced,
            "CONFIRMED": TestReproOutcome.confirmed,
        },
        TestReproOutcome.none,
    )


def parse_test_ux(text: str) -> TestUxOutcome:
    """A UX adjudication `UX: IMPROVABLE|CONSTRAINED|DISAGREE`."""
    return _last_marker(
        text,
        "UX",
        {
            "IMPROVABLE": TestUxOutcome.improvable,
            "CONSTRAINED": TestUxOutcome.constrained,
            "DISAGREE": TestUxOutcome.disagree,
        },
        TestUxOutcome.none,
    )


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str  # migration note: still the work-item id for most tasks
    work_item_id: str = ""
    run_id: str = ""
    repo: str  # git URL the runner checks out
    branch: str = ""  # non-default branch to check out (PR-mode work and its verify/fix)
    fresh_branch: bool = False  # reset an existing task branch to default before running
    kind: TaskKind = TaskKind.work
    instructions: str
    conversation_id: str = ""
    conversation_turn: str = ""  # intake: initial | message | proceed | write_mission
    session_handle: str = ""  # runner resumes this backend session when possible
    issue_number: int = 0  # issue solving: the issue this task resolves/reviews
    issue_doc: str = ""  # issue solving: full issue markdown (title+body+comments) -> .hive ISSUE.md
    issue_attachments: list[str] = []  # issue solving: image filenames the runner fetches from the chief
    required_capabilities: list[str] = []  # testing: runner capabilities such as browser/docker
    backend: str = "cursor"  # kodo backend name: claude | cursor | codex | gemini-cli
    model: str = ""  # backend default when empty
    status: TaskStatus = TaskStatus.pending
    runner_id: str = ""
    delivered: bool = False  # runner has picked the assignment up via poll
    cancel_requested: bool = False  # operator asked to stop; runner honors cooperatively
    verdict: Verdict = Verdict.none  # parsed from a verify task's result
    trace_blob: str = ""  # blob key of the kodo JSONL run trace, once uploaded
    artifact_blobs: list[str] = []  # artifact filenames uploaded by the runner for this task
    result_text: str = ""
    is_error: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    structured_result: dict = Field(default_factory=dict)
    structured_result_error: str = ""
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
    capabilities: list[str] = []  # runner-local capabilities such as browser/docker
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
    browser_status: ResourceUsability = ResourceUsability.unknown
    browser_probe_at: float = 0.0
    browser_probe_text: str = ""
    docker_status: ResourceUsability = ResourceUsability.unknown
    docker_probe_at: float = 0.0
    docker_probe_text: str = ""
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

    def supports(self, capabilities: list[str]) -> bool:
        for capability in capabilities:
            if capability == "browser" and self.browser_status != ResourceUsability.usable:
                return False
            if capability == "docker" and self.docker_status != ResourceUsability.usable:
                return False
        return True

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


class LicensingMode(StrEnum):
    """How a subscription's credential may be placed across machines.

    Decides whether Hive can stand up an agent itself or must ask the human to
    log in on a specific machine (see CONTEXT.md "Licensing Mode").
    """

    portable = "portable"  # API key Hive can copy to any machine (e.g. Cursor, Gemini key)
    machine_bound = "machine_bound"  # login tied to where the human authed (e.g. Claude Max)
    unknown = "unknown"


class Subscription(BaseModel):
    """An AI subscription the user owns (Claude Max, ChatGPT/codex, Cursor...).
    The durable, account-level access an Agent is authenticated against — the
    user's longest-lived unit of capacity. Informs orchestration about capacity
    that exists beyond what runners currently advertise, and anchors login-todos
    for remote nodes."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    provider: str  # backend name it powers: claude | codex | cursor | gemini-cli
    plan: str = ""  # e.g. "ChatGPT Plus", "Claude Max 5x"
    licensing_mode: LicensingMode = LicensingMode.unknown
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


class IssueRunStatus(StrEnum):
    scanning = "scanning"
    queued = "queued"
    running = "running"
    blocked = "blocked"
    done = "done"
    cancelled = "cancelled"
    failed = "failed"


class IssueRunScope(StrEnum):
    selected = "selected"
    all_open_now = "all_open_now"
    scan_only = "scan_only"


class IssueRun(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str
    repo: str
    scope: IssueRunScope = IssueRunScope.all_open_now
    issue_numbers: list[int] = []
    status: IssueRunStatus = IssueRunStatus.queued
    counts: dict = {}
    created_at: float = Field(default_factory=now)
    started_at: float = 0.0
    finished_at: float = 0.0


class StoryStatus(StrEnum):
    untested = "untested"
    passing = "passing"
    failing = "failing"
    blocked = "blocked"
    stale = "stale"
    archived = "archived"


class StoryCentrality(StrEnum):
    core = "core"
    major = "major"
    minor = "minor"


class StoryFidelity(StrEnum):
    none = "none"
    local = "local"
    docker = "docker"


class StoryOracleStatus(StrEnum):
    trusted = "trusted"
    draft = "draft"


class Story(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str
    repo: str = ""
    key: str
    title: str = ""
    intent: str = ""
    acceptance: str = ""
    spec_ref: str = ""
    tags: list[str] = []
    status: StoryStatus = StoryStatus.untested
    centrality: StoryCentrality = StoryCentrality.major
    centrality_locked: bool = False
    oracle_status: StoryOracleStatus = StoryOracleStatus.trusted
    oracle_status_reason: str = ""
    spec_baseline: str = ""
    blessed: bool = False
    blessed_at: float = 0.0
    last_tested_baseline: str = ""
    last_fidelity: StoryFidelity = StoryFidelity.none
    open_issue_number: int = 0
    open_issue_url: str = ""
    known_limitations: list[str] = []
    last_episode_id: str = ""
    last_result_task_id: str = ""
    last_tested_at: float = 0.0
    order: int = 0
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


class TestEpisodeStatus(StrEnum):
    refreshing = "refreshing"
    sweeping = "sweeping"
    confirming = "confirming"
    done = "done"
    cancelled = "cancelled"
    failed = "failed"


class TestEpisodeScope(StrEnum):
    priority = "priority"
    full = "full"
    selected = "selected"


class TestEpisode(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str
    repo: str
    scope: TestEpisodeScope = TestEpisodeScope.priority
    story_keys: list[str] = []
    selected_story_keys: list[str] = []
    max_stories: int = 0
    status: TestEpisodeStatus = TestEpisodeStatus.refreshing
    refresh_backend: str = "codex"
    refresh_model: str = ""
    sweep_backend: str = "codex"
    sweep_model: str = ""
    confirm_backend: str = "codex"
    confirm_model: str = ""
    counts: dict = {}
    created_at: float = Field(default_factory=now)
    started_at: float = 0.0
    finished_at: float = 0.0


class FindingKind(StrEnum):
    bug = "bug"
    ux_smell = "ux_smell"


class FindingStatus(StrEnum):
    suspected = "suspected"
    confirmed = "confirmed"
    blocked = "blocked"
    rejected = "rejected"
    constrained = "constrained"
    duplicate = "duplicate"
    resolved = "resolved"  # was confirmed; the story later re-tested green and its issue was closed


class Finding(BaseModel):
    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    workstream_id: str
    repo: str = ""
    episode_id: str
    story_key: str
    kind: FindingKind = FindingKind.bug
    severity: str = "medium"
    summary: str
    expected: str = ""  # what should have happened, per the rule/example
    actual: str = ""  # what happened instead
    detail: str = ""  # steps to reproduce
    oracle: str = ""
    evidence_blobs: list[str] = []
    status: FindingStatus = FindingStatus.suspected
    issue_number: int = 0
    issue_url: str = ""
    sweep_task_id: str = ""
    confirm_task_id: str = ""
    signature: str = ""
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


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
    tracked on Task; this is the chief side of the bill)."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    created_at: float = Field(default_factory=now)


class DirectiveStatus(StrEnum):
    triaging = "triaging"  # received; not yet handed to a pipeline (see routing_note)
    working = "working"  # filed as a GitHub issue; the issue pipeline owns it
    done = "done"  # the issue landed (merged + closed by Hive)
    cancelled = "cancelled"  # the issue was closed outside Hive without landing


class Directive(BaseModel):
    """A persisted, human-authored ask to a project — "just tell Hive what you
    want" (see CONTEXT.md "Directive"). Hive files it as a GitHub issue on the
    project repo (the issue is the durable record) and the proven issue
    pipeline (resolve → review → merge) tracks it to done. Distinct from the
    iteration goal (standing strategy) and from issues authored on GitHub
    (external origin) — a directive is the user's direct ask through Hive."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    project_id: str
    text: str
    status: DirectiveStatus = DirectiveStatus.triaging
    issue_number: int = 0  # the GitHub issue Hive filed for this ask
    issue_url: str = ""
    routing_note: str = ""  # one line: where this stands / what it needs
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


class Checkout(BaseModel):
    """A project repo's working copy on one machine — the unit that answers
    "where does this project physically exist, and is any work there missing
    from the remote?" (see CONTEXT.md "Checkout"). One per (machine, repo).

    The runner reports the git facts in its heartbeat; the chief upserts
    this record. The remote is authoritative — a checkout is observed, not a
    source of truth. Unpushed commits (`ahead > 0`) or a `dirty` tree are the
    signal that real work may live only here."""

    id: str = Field(default_factory=new_id)
    workspace_id: str = DEFAULT_WORKSPACE_ID
    machine_id: str
    repo: str  # git URL
    exists: bool = False
    head_sha: str = ""
    branch: str = ""
    ahead: int = 0  # local commits not on origin
    behind: int = 0  # origin commits not local
    dirty: bool = False  # uncommitted working-tree changes
    env_status: str = "unknown"  # reserved: dependency-setup readiness
    last_reported_at: float = Field(default_factory=now)
