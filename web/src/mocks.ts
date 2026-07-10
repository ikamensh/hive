// Canned fixtures served when VITE_MOCK=1. Mutations update in-memory state so
// the UI stays interactive for screenshots and offline development.

import type {
  AgentConversation,
  AuthInfo,
  Checkout,
  CiCheckResult,
  DecisionLedger,
  Directive,
  GithubRepo,
  HumanTodo,
  IntakeMessage,
  IssueRun,
  LicensingMode,
  MachineGroup,
  Overview,
  Project,
  ProjectCreate,
  ProjectDetail,
  ProjectPatch,
  ProjectRepoCreate,
  PreflightResult,
  Question,
  ResourcesPayload,
  ScanResult,
  Finding,
  Story,
  Subscription,
  SubscriptionCandidate,
  TestabilityView,
  TestingHealth,
  Task,
  TestEpisode,
  TestEpisodeResult,
  VersionInfo,
  WorkItem,
  Workstream,
  WorkstreamPatch,
  WorkspaceMember,
  WorkspaceRole,
} from "./types";

const now = Date.now() / 1000;
const version: VersionInfo = {
  version: "0.1.150+mock",
  base_version: "0.1",
  major: 0,
  minor: 1,
  micro: 150,
  commit: "mock",
  dirty: false,
  source: "mock",
};

const mockGithubRepos: GithubRepo[] = [
  {
    full_name: "acme/atlas-spec",
    ssh_url: "git@github.com:acme/atlas-spec.git",
    clone_url: "https://github.com/acme/atlas-spec.git",
    private: true,
    description: "Atlas product spec home",
  },
  {
    full_name: "acme/atlas-api",
    ssh_url: "git@github.com:acme/atlas-api.git",
    clone_url: "https://github.com/acme/atlas-api.git",
    private: true,
    description: "Atlas backend",
  },
  {
    full_name: "acme/atlas-web",
    ssh_url: "git@github.com:acme/atlas-web.git",
    clone_url: "https://github.com/acme/atlas-web.git",
    private: false,
    description: "Atlas frontend",
  },
  {
    full_name: "acme/relay",
    ssh_url: "git@github.com:acme/relay.git",
    clone_url: "https://github.com/acme/relay.git",
    private: true,
    description: "Relay service",
  },
  {
    full_name: "acme/beacon",
    ssh_url: "git@github.com:acme/beacon.git",
    clone_url: "https://github.com/acme/beacon.git",
    private: false,
    description: "Beacon — issue-solving demo repo",
  },
];

const projects: Project[] = [
  {
    id: "p-atlas",
    name: "atlas",
    spec_repo: "git@github.com:acme/atlas-spec.git",
    member_repos: ["git@github.com:acme/atlas-api.git", "git@github.com:acme/atlas-web.git"],
    mode: "build",
    autonomy: "direct_push",
    guess_propensity: "sometimes",
    prod_deploys: false,
    ci_autofix: false,
    testing_auto: true,
    paused: false,
    archived: false,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
    agent_grants: [
      { backends: [], models: [], sessions_per_day: 5 },
      { backends: ["claude"], models: ["haiku"], sessions_per_day: null },
    ],
    intake_conversation_id: "conv-atlas",
    state: "needs_attention",
    created_at: now - 86400 * 6,
  },
  {
    id: "p-relay",
    name: "relay",
    spec_repo: "git@github.com:acme/relay.git",
    member_repos: ["git@github.com:acme/relay.git"],
    mode: "build",
    autonomy: "pr",
    guess_propensity: "often",
    prod_deploys: true,
    ci_autofix: false,
    testing_auto: true,
    paused: false,
    archived: false,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
    agent_grants: [
      { backends: [], models: [], sessions_per_day: 5 },
      { backends: ["codex"], models: ["gpt-5.4-mini"], sessions_per_day: null },
    ],
    intake_conversation_id: "",
    state: "working",
    created_at: now - 86400 * 2,
  },
  {
    id: "p-ledger",
    name: "ledger",
    spec_repo: "git@github.com:acme/ledger-spec.git",
    member_repos: ["git@github.com:acme/ledger.git"],
    mode: "maintain",
    autonomy: "direct_push",
    guess_propensity: "rarely",
    prod_deploys: true,
    ci_autofix: false,
    testing_auto: true,
    paused: false,
    archived: false,
    goal_complete: true,
    goal_complete_note:
      "All v1 invoicing flows shipped and verified. Remaining backlog is empty; test suite green across both repos.",
    daily_budget_usd: 0,
    intake_conversation_id: "",
    state: "idle_goal_complete",
    created_at: now - 86400 * 30,
  },
  {
    id: "p-probe",
    name: "probe",
    spec_repo: "git@github.com:acme/probe.git",
    member_repos: [],
    mode: "build",
    autonomy: "direct_push",
    guess_propensity: "never",
    prod_deploys: false,
    ci_autofix: false,
    testing_auto: true,
    paused: true,
    archived: false,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
    intake_conversation_id: "",
    state: "blocked_resources",
    created_at: now - 3600 * 5,
  },
  {
    id: "p-beacon",
    name: "beacon",
    spec_repo: "git@github.com:acme/beacon.git",
    member_repos: [],
    mode: "maintain",
    autonomy: "direct_push",
    guess_propensity: "rarely",
    prod_deploys: false,
    ci_autofix: false,
    testing_auto: true,
    paused: false,
    archived: false,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
    intake_conversation_id: "",
    state: "needs_attention",
    created_at: now - 86400 * 3,
  },
];

const decisionLedgers: Record<string, DecisionLedger> = {
  "p-atlas": {
    decisions: [
      {
        id: "ATL-001",
        title: "Paid pilot pricing uses hybrid metering",
        source_type: "user_provided",
        impact: "high",
        reversibility: "medium",
        status: "accepted",
        expires_when: "when packaging changes after the first pilots",
        trace: "input-log/2026-07-01-pricing.md",
        body: "Atlas starts with a base seat fee plus compute overage. Pure per-call billing is out for the first paid pilots.",
        can_reopen: false,
      },
      {
        id: "ATL-002",
        title: "Webhook retries expire after 24 hours",
        source_type: "agent_proposed",
        impact: "medium",
        reversibility: "high",
        status: "accepted_for_iteration",
        expires_when: "operator specifies a customer-facing retry policy",
        trace: "input-log/2026-07-02-hardening.md#retry-policy",
        body: "Failed webhooks are retried with backoff for 24 hours, then surfaced in the admin audit log.",
        can_reopen: true,
      },
      {
        id: "ATL-003",
        title: "Refresh rotation keeps a twelve-hour sliding cap",
        source_type: "code_derived",
        impact: "medium",
        reversibility: "medium",
        status: "accepted",
        expires_when: "auth threat model changes",
        trace: "atlas-api/auth/session.ts",
        body: "The existing API already enforces a twelve-hour sliding cap. Hive keeps that policy unless the operator changes the auth model.",
        can_reopen: true,
      },
      {
        id: "ATL-004",
        title: "Onboarding setup can stay single-org for the first pass",
        source_type: "inferred",
        impact: "low",
        reversibility: "high",
        status: "accepted_for_iteration",
        expires_when: "multi-org invites enter the iteration goal",
        trace: "wiki/onboarding.md",
        body: "The onboarding wizard assumes one organization per pilot account for this iteration.",
        can_reopen: true,
      },
    ],
    counts: { total: 4, operator_specified: 1, hive_assumed: 3, reopenable: 3 },
    source_types: ["agent_proposed", "code_derived", "inferred", "user_provided"],
    must_ask: [
      "who is authorized to perform an action (permission/auth model)",
      "billing, pricing, or seat behavior",
      "data retention and destructive defaults (hard vs soft delete)",
      "public API contracts and breaking changes",
      "legal/compliance wording and notices",
      "security-sensitive defaults (e.g. token handling, account-existence leaks)",
      "customer-facing pricing changes",
    ],
    error: "",
  },
};

const conversations: AgentConversation[] = [
  {
    id: "conv-atlas",
    project_id: "p-atlas",
    role: "intake",
    repo: "git@github.com:acme/atlas-spec.git",
    backend: "claude",
    model: "opus",
    status: "open",
    session_handle: "mock-atlas-intake",
    latest_brief:
      "## What I understand\n\nAtlas is a multi-repo SaaS project. The long-term mission is to make customer usage, auth, and onboarding reliable enough for paid pilots.\n\n## Next iteration\n\nShip a small hardening pass: refresh-token rotation, billing-meter decisions, and onboarding validation fixes.\n\n## Needs you\n\nConfirm the pricing unit before billing work starts.",
    transcript: [
      {
        role: "assistant",
        text: "I inspected the specs and current repos. Atlas is ready for a hardening-focused first iteration, pending the pricing-unit decision.",
      },
    ],
    last_task_id: "t-intake-1",
    created_at: now - 86400 * 6 + 120,
    updated_at: now - 3600,
  },
];

const projectWorkstreams: Workstream[] = [
  {
    id: "stream-atlas-iteration",
    project_id: "p-atlas",
    kind: "iteration",
    title: "Iteration goal",
    repo: "",
    source_ref: {},
    status: "active",
    enabled: true,
    config: {},
    created_at: now - 86400 * 6,
    updated_at: now - 3600,
  },
  {
    id: "stream-beacon-iteration",
    project_id: "p-beacon",
    kind: "iteration",
    title: "Iteration goal",
    repo: "",
    source_ref: {},
    status: "idle",
    enabled: true,
    config: {},
    created_at: now - 86400 * 8,
    updated_at: now - 3600,
  },
  {
    id: "stream-beacon-issues",
    project_id: "p-beacon",
    kind: "github_issues",
    title: "GitHub issues: acme/beacon",
    repo: "https://github.com/acme/beacon.git",
    source_ref: { provider: "github", issues: true },
    status: "active",
    enabled: true,
    config: {},
    created_at: now - 86400 * 8,
    updated_at: now - 1800,
  },
  {
    id: "stream-beacon-testing",
    project_id: "p-beacon",
    kind: "testing",
    title: "Testing: beacon",
    repo: "https://github.com/acme/beacon.git",
    source_ref: { acceptance_dir: "acceptance" },
    status: "active",
    enabled: true,
    config: { fidelity: "local" },
    created_at: now - 86400 * 2,
    updated_at: now - 1200,
  },
];

const stories: Story[] = [
  {
    id: "story-login-redirect",
    project_id: "p-beacon",
    workstream_id: "stream-beacon-testing",
    repo: "https://github.com/acme/beacon.git",
    key: "login-redirect",
    title: "Login redirect",
    intent: "As a user I can sign in from a deep link so that I continue where I started.",
    acceptance: "## Examples\n- Given I open /reports/usage while signed out\n  When I sign in\n  Then I land on /reports/usage",
    spec_ref: "acceptance/login-redirect.md",
    tags: ["ui"],
    status: "failing",
    centrality: "core",
    centrality_locked: false,
    spec_baseline: "b1",
    blessed: false,
    blessed_at: 0,
    last_tested_baseline: "b1",
    last_fidelity: "local",
    open_issue_number: 42,
    open_issue_url: "https://github.com/acme/beacon/issues/42",
    known_limitations: [],
    last_episode_id: "test-episode-1",
    last_result_task_id: "task-test-sweep-1",
    last_tested_at: now - 3600 * 7,
    order: 1,
    created_at: now - 86400 * 2,
    updated_at: now - 3600 * 7,
  },
  {
    id: "story-audit-filter",
    project_id: "p-beacon",
    workstream_id: "stream-beacon-testing",
    repo: "https://github.com/acme/beacon.git",
    key: "audit-filter-refresh",
    title: "Audit filters survive refresh",
    intent: "As an admin I can refresh the audit log without losing filters.",
    acceptance: "## Examples\n- Given I filter the audit log by actor\n  When I refresh the page\n  Then the same filtered view remains visible",
    spec_ref: "acceptance/audit-filter-refresh.md",
    tags: ["ui"],
    status: "untested",
    centrality: "major",
    centrality_locked: false,
    spec_baseline: "b1",
    blessed: false,
    blessed_at: 0,
    last_tested_baseline: "",
    last_fidelity: "none",
    open_issue_number: 0,
    open_issue_url: "",
    known_limitations: [],
    last_episode_id: "",
    last_result_task_id: "",
    last_tested_at: 0,
    order: 2,
    created_at: now - 86400,
    updated_at: now - 86400,
  },
  {
    id: "story-webhook-retry",
    project_id: "p-beacon",
    workstream_id: "stream-beacon-testing",
    repo: "https://github.com/acme/beacon.git",
    key: "webhook-retry",
    title: "Webhook retries",
    intent: "As an integrator I get reliable retry attempts for failed webhook deliveries.",
    acceptance: "## Examples\n- Given a webhook endpoint returns 500\n  When Beacon schedules retries\n  Then attempts back off and continue beyond the first retry",
    spec_ref: "acceptance/webhook-retry.md",
    tags: ["api"],
    status: "passing",
    centrality: "major",
    centrality_locked: false,
    spec_baseline: "b1",
    blessed: false,
    blessed_at: 0,
    last_tested_baseline: "b1",
    last_fidelity: "docker",
    open_issue_number: 0,
    open_issue_url: "",
    known_limitations: [],
    last_episode_id: "test-episode-1",
    last_result_task_id: "task-test-sweep-2",
    last_tested_at: now - 3600 * 9,
    order: 3,
    created_at: now - 86400,
    updated_at: now - 3600 * 9,
  },
];

const findings: Finding[] = [
  {
    id: "finding-login-redirect",
    project_id: "p-beacon",
    workstream_id: "stream-beacon-testing",
    repo: "https://github.com/acme/beacon.git",
    episode_id: "test-episode-1",
    story_key: "login-redirect",
    kind: "bug",
    severity: "high",
    summary: "Deep-link redirect is lost after login",
    expected: "After login the user returns to the deep link they requested (/reports/usage).",
    actual: "After login the user lands on / instead of the requested deep link.",
    detail: "Opening /reports/usage while signed out lands on / after authentication.",
    oracle: "The login-redirect acceptance example requires returning to the deep link.",
    evidence_blobs: ["login-redirect-console.txt"],
    status: "confirmed",
    issue_number: 42,
    issue_url: "https://github.com/acme/beacon/issues/42",
    sweep_task_id: "task-test-sweep-1",
    confirm_task_id: "task-test-repro-1",
    signature: "login-redirect",
    created_at: now - 3600 * 8,
    updated_at: now - 3600 * 7,
  },
];

const testEpisodes: TestEpisode[] = [
  {
    id: "test-episode-1",
    project_id: "p-beacon",
    workstream_id: "stream-beacon-testing",
    repo: "https://github.com/acme/beacon.git",
    scope: "priority",
    story_keys: ["login-redirect", "webhook-retry"],
    selected_story_keys: [],
    max_stories: 5,
    status: "done",
    refresh_backend: "codex",
    refresh_model: "",
    sweep_backend: "codex",
    sweep_model: "",
    confirm_backend: "codex",
    confirm_model: "",
    counts: { stories_passing: 1, stories_failing: 1, findings_confirmed: 1 },
    created_at: now - 3600 * 10,
    started_at: now - 3600 * 10,
    finished_at: now - 3600 * 7,
  },
];

const directives: Directive[] = [
  {
    id: "dir-atlas-1",
    project_id: "p-atlas",
    text: "Add a dark-mode toggle to the settings page and persist the choice.",
    status: "working",
    issue_number: 41,
    issue_url: "https://github.com/acme/atlas/issues/41",
    routing_note: "filed issue #41; resolve task queued",
    created_at: now - 60 * 8,
    updated_at: now - 60 * 8,
  },
  {
    id: "dir-atlas-2",
    project_id: "p-atlas",
    text: "Investigate why the nightly export job is 3x slower since Tuesday.",
    status: "triaging",
    issue_number: 0,
    issue_url: "",
    routing_note: "needs attention: GitHub token can't create issues on acme/atlas",
    created_at: now - 60 * 2,
    updated_at: now - 60 * 2,
  },
];

const checkouts: Checkout[] = [
  // atlas-spec: clean on the always-on server, drifted on the laptop.
  {
    id: "co-1", machine_id: "m-hex1", repo: "git@github.com:acme/atlas-spec.git",
    exists: true, head_sha: "9f3a1c2", branch: "main", ahead: 0, behind: 0, dirty: false,
    env_status: "unknown", last_reported_at: now - 14,
  },
  {
    id: "co-2", machine_id: "m-hex2", repo: "git@github.com:acme/atlas-spec.git",
    exists: true, head_sha: "1b77e09", branch: "main", ahead: 3, behind: 1, dirty: true,
    env_status: "unknown", last_reported_at: now - 60 * 47,
  },
  // atlas-api: only checked out on the server, one unpushed commit.
  {
    id: "co-3", machine_id: "m-hex1", repo: "git@github.com:acme/atlas-api.git",
    exists: true, head_sha: "44c0d8e", branch: "hive/issue-42", ahead: 1, behind: 0, dirty: false,
    env_status: "unknown", last_reported_at: now - 20,
  },
  // atlas-web: clean on the server, never checked out on the laptop.
  {
    id: "co-4", machine_id: "m-hex1", repo: "git@github.com:acme/atlas-web.git",
    exists: true, head_sha: "0aa91f4", branch: "main", ahead: 0, behind: 0, dirty: false,
    env_status: "unknown", last_reported_at: now - 31,
  },
];

function canonicalRepo(url: string): string {
  return url
    .trim()
    .replace(/^git@github\.com:/, "")
    .replace(/^ssh:\/\/git@github\.com\//, "")
    .replace(/^https?:\/\/github\.com\//, "")
    .replace(/\.git$/, "")
    .replace(/\/$/, "")
    .toLowerCase();
}

function checkoutsForProject(projectId: string): Checkout[] {
  const project = projects.find((p) => p.id === projectId);
  if (!project) return [];
  const repos = new Set([project.spec_repo, ...project.member_repos].map(canonicalRepo));
  return checkouts.filter((c) => repos.has(canonicalRepo(c.repo)));
}

const workItems: WorkItem[] = [
  {
    id: "ws-auth",
    project_id: "p-atlas",
    title: "Auth & session hardening",
    description: "Move to short-lived tokens, add refresh rotation.",
    status: "active",
    parked_reason: "",
    created_at: now - 86400 * 5,
  },
  {
    id: "ws-billing",
    project_id: "p-atlas",
    title: "Usage-based billing",
    description: "Metering pipeline + Stripe integration.",
    status: "parked",
    parked_reason: "Waiting on pricing decision (open question).",
    created_at: now - 86400 * 4,
  },
  {
    id: "ws-onboard",
    project_id: "p-atlas",
    title: "Onboarding flow",
    description: "Guided setup wizard for new orgs.",
    status: "done",
    parked_reason: "",
    created_at: now - 86400 * 6,
  },
  {
    id: "ws-issue-42",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 42,
    issue_url: "https://github.com/acme/beacon/issues/42",
    title: "#42 Login redirect drops the `next` query param",
    description:
      "## #42 Login redirect drops the `next` query param\n\nWhen an unauthenticated user hits a deep link, we bounce them to `/login` but the `?next=` param is lost, so after sign-in they always land on the dashboard.\n\n**Steps to reproduce**\n\n1. Open `/reports/usage` while logged out\n2. Sign in\n3. You land on `/` instead of `/reports/usage`\n\n---\n\n**comment by @maintainer:**\nLikely the `LoginForm` submit handler isn't forwarding `location.state.from`.",
    status: "resolving",
    parked_reason: "",
    created_at: now - 3600 * 8,
  },
  {
    id: "ws-issue-51",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 51,
    issue_url: "https://github.com/acme/beacon/issues/51",
    title: "#51 Add export-to-CSV for the audit log",
    description:
      "## #51 Add export-to-CSV for the audit log\n\nIt'd be great to export the audit log to CSV from the admin view.\n\nNo strong opinion on columns or filtering — whatever is easiest.",
    status: "blocked_clarity",
    parked_reason:
      "Underspecified feature: which columns, time range, and whether to respect the active filter all need a product decision. Commented on the issue asking.",
    created_at: now - 3600 * 30,
  },
  {
    id: "ws-issue-46",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 46,
    issue_url: "https://github.com/acme/beacon/issues/46",
    order: 46,
    title: "#46 Webhook retries stop after one attempt",
    description:
      "## #46 Webhook retries stop after one attempt\n\nFailed delivery attempts should back off and retry, but only the first retry is scheduled.",
    status: "queued",
    parked_reason: "",
    created_at: now - 3600 * 10,
  },
  {
    id: "ws-issue-37",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 37,
    issue_url: "https://github.com/acme/beacon/issues/37",
    title: "#37 Dark-mode toggle flickers on first paint",
    description:
      "## #37 Dark-mode toggle flickers on first paint\n\nThe theme is read from `localStorage` in a `useEffect`, so the light theme paints for one frame before switching to dark. Set the theme before first paint (inline script in `<head>`).",
    status: "reviewing",
    parked_reason: "",
    created_at: now - 3600 * 5,
  },
  {
    id: "ws-issue-29",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 29,
    issue_url: "https://github.com/acme/beacon/issues/29",
    title: "#29 Rate limiter rejects valid bursts",
    description:
      "## #29 Rate limiter rejects valid bursts\n\nThe token-bucket refill math is off by a factor of the window size, so legitimate bursts get 429s.",
    status: "rejected",
    parked_reason:
      "Review rejected: the fix widened the bucket but broke the per-IP isolation test. Recommended approach: fix the refill rate, keep buckets per-IP. Commented on the issue.",
    created_at: now - 86400 * 2,
  },
  {
    id: "ws-issue-64",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 64,
    issue_url: "https://github.com/acme/beacon/issues/64",
    title: "#64 Audit-log filters reset after refresh",
    description:
      "## #64 Audit-log filters reset after refresh\n\nFilters are kept only in component state. Preserve them in the URL so a refresh or shared link keeps the same view.",
    status: "queued",
    parked_reason: "",
    created_at: now - 3600 * 3,
  },
  {
    id: "ws-issue-18",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 18,
    issue_url: "https://github.com/acme/beacon/issues/18",
    title: "#18 Typo in onboarding email subject",
    description:
      "## #18 Typo in onboarding email subject\n\n\"Welcom to Beacon\" → \"Welcome to Beacon\".",
    status: "done",
    parked_reason: "",
    created_at: now - 86400 * 4,
  },
  {
    id: "ws-issue-12",
    project_id: "p-beacon",
    source: "issue",
    issue_number: 12,
    issue_url: "https://github.com/acme/beacon/issues/12",
    title: "#12 Investigate flaky upload test",
    description:
      "## #12 Investigate flaky upload test\n\nClosed by a maintainer — turned out to be a CI runner issue, not a product bug.",
    status: "cancelled",
    parked_reason: "Issue closed on GitHub by a human.",
    created_at: now - 86400 * 6,
  },
];

for (const item of workItems) {
  if (item.project_id === "p-beacon" && item.source === "issue") {
    item.workstream_id = "stream-beacon-issues";
    item.repo = "https://github.com/acme/beacon.git";
  }
}

const issueRuns: IssueRun[] = [
  {
    id: "run-beacon-1",
    project_id: "p-beacon",
    workstream_id: "stream-beacon-issues",
    repo: "https://github.com/acme/beacon.git",
    scope: "all_open_now",
    issue_numbers: [42, 51, 46, 37, 29, 64],
    status: "running",
    counts: { running: 2, blocked: 2, queued: 2 },
    created_at: now - 3600 * 8,
    started_at: now - 3600 * 8,
    finished_at: 0,
  },
];

const questions: Question[] = [
  {
    id: "q-pricing",
    project_id: "p-atlas",
    workstream_id: "ws-billing",
    text: "## Pricing model for metered billing\n\nThe spec says \"usage-based\" but doesn't define the unit.\n\n**Options:**\n\n1. Per API call — simple, but penalizes chatty clients\n2. Per compute-second — fair, harder to explain\n3. Hybrid: base seat fee + compute overage\n\n**Recommendation:** option 3; matches what the two reference competitors do.",
    status: "open",
    answer: "",
    created_at: now - 3600 * 4,
    answered_at: 0,
  },
  {
    id: "q-region",
    project_id: "p-atlas",
    workstream_id: "",
    text: "## EU customer data residency\n\nThe spec's compliance section is silent on whether disaster recovery may leave the EU.\n\n**Options:**\n\n1. Keep EU customer data in `eu-west` exclusively, including backups and DR.\n2. Allow encrypted replication to `us-east` for disaster recovery only.\n\n**Recommendation:** option 1 for the first paid pilots; it is easier to explain and safer to relax later than to claw back.",
    status: "open",
    answer: "",
    created_at: now - 1800,
    answered_at: 0,
  },
  {
    id: "q-old",
    project_id: "p-atlas",
    workstream_id: "ws-auth",
    text: "Token TTL: spec says \"short-lived\" — 15 min or 1 h?",
    status: "answered",
    answer: "15 minutes, with sliding refresh up to 12 h.",
    created_at: now - 86400 * 2,
    answered_at: now - 86400 * 2 + 5400,
  },
  {
    id: "q-testability-stripe",
    project_id: "p-beacon",
    workstream_id: "stream-beacon-testing",
    dedup_key: "testability:stream-beacon-testing:stripe-sandbox",
    text: "Hive needs a decision to finish the testability contract for `acme/beacon`.\n\n**May test sweeps exercise the Stripe integration?**\n\nOptions:\n- A) Create a Stripe sandbox key and store it as `STRIPE_TEST_KEY` on the runners\n- B) Skip payment stories in sweeps; mark them human-demo-only\n\nHive recommends: A\n\nAnswer here — Hive updates `testability.md` itself and re-proves it.",
    status: "open",
    answer: "",
    created_at: now - 900,
    answered_at: 0,
  },
];

const tasks: Task[] = [
  {
    id: "t-intake-1",
    project_id: "p-atlas",
    workstream_id: "",
    repo: "git@github.com:acme/atlas-spec.git",
    branch: "",
    fresh_branch: false,
    kind: "intake",
    instructions: "Inspect the repo and state mission, next iteration, next steps, and material questions.",
    conversation_id: "conv-atlas",
    conversation_turn: "initial",
    session_handle: "mock-atlas-intake",
    backend: "claude",
    model: "opus",
    status: "done",
    runner_id: "r-hex1",
    delivered: true,
    cancel_requested: false,
    verdict: "none",
    trace_blob: "traces/t-intake-1.jsonl",
    result_text:
      "## What I understand\n\nAtlas is a multi-repo SaaS project. The long-term mission is to make customer usage, auth, and onboarding reliable enough for paid pilots.\n\n## Next iteration\n\nShip a small hardening pass: refresh-token rotation, billing-meter decisions, and onboarding validation fixes.\n\n## Needs you\n\nConfirm the pricing unit before billing work starts.",
    is_error: false,
    cost_usd: 0.44,
    input_tokens: 62000,
    output_tokens: 3400,
    prompt_versions: { intake: "mock" },
    created_at: now - 3600 * 5,
    started_at: now - 3600 * 5 + 20,
    finished_at: now - 3600 * 4,
  },
  {
    id: "t-1",
    project_id: "p-atlas",
    workstream_id: "ws-auth",
    repo: "git@github.com:acme/atlas-api.git",
    branch: "",
    fresh_branch: false,
    kind: "work",
    instructions: "Implement refresh token rotation per spec §4.2",
    conversation_id: "",
    conversation_turn: "",
    session_handle: "",
    backend: "claude",
    model: "",
    status: "done",
    runner_id: "r-hex1",
    delivered: true,
    cancel_requested: false,
    verdict: "none",
    trace_blob: "traces/t-1.jsonl",
    result_text:
      "## Summary\n\nImplemented refresh-token rotation:\n\n- `POST /auth/refresh` now invalidates the presented token family on reuse\n- Added `token_families` table + migration `0042`\n- 18 new tests, all green\n\n```bash\npytest tests/auth -q\n..................  18 passed in 4.2s\n```",
    is_error: false,
    cost_usd: 1.87,
    input_tokens: 412000,
    output_tokens: 38000,
    prompt_versions: { landing_direct_push: "mock" },
    created_at: now - 7200,
    started_at: now - 7100,
    finished_at: now - 5400,
  },
  {
    id: "t-2",
    project_id: "p-atlas",
    workstream_id: "ws-auth",
    repo: "git@github.com:acme/atlas-api.git",
    branch: "",
    fresh_branch: false,
    kind: "verify",
    instructions: "Verify refresh rotation against spec §4.2",
    conversation_id: "",
    conversation_turn: "",
    session_handle: "",
    backend: "codex",
    model: "",
    status: "running",
    runner_id: "r-hex1",
    delivered: true,
    cancel_requested: false,
    verdict: "none",
    trace_blob: "",
    result_text: "",
    is_error: false,
    cost_usd: 0,
    input_tokens: 0,
    output_tokens: 0,
    prompt_versions: { verify_suffix: "mock" },
    created_at: now - 900,
    started_at: now - 840,
    finished_at: 0,
  },
  {
    id: "t-3",
    project_id: "p-atlas",
    workstream_id: "ws-onboard",
    repo: "git@github.com:acme/atlas-web.git",
    branch: "",
    fresh_branch: false,
    kind: "work",
    instructions: "Fix wizard step 3 validation",
    conversation_id: "",
    conversation_turn: "",
    session_handle: "",
    backend: "cursor",
    model: "",
    status: "failed",
    runner_id: "r-hex2",
    delivered: true,
    cancel_requested: false,
    verdict: "none",
    trace_blob: "traces/t-3.jsonl",
    result_text: "Runner timeout after 45 min: `npm install` hung resolving private registry. Resource marked exhausted.",
    is_error: true,
    cost_usd: 0.32,
    input_tokens: 90000,
    output_tokens: 4000,
    prompt_versions: { landing_direct_push: "mock" },
    created_at: now - 86400,
    started_at: now - 86400 + 60,
    finished_at: now - 86400 + 2760,
  },
  {
    id: "t-r1",
    project_id: "p-beacon",
    workstream_id: "ws-issue-42",
    repo: "git@github.com:acme/beacon.git",
    branch: "hive/issue-42",
    fresh_branch: true,
    kind: "resolve",
    instructions: "Clarify then fix issue #42 (login redirect drops `next`). See .hive/issue-42/ISSUE.md.",
    conversation_id: "",
    conversation_turn: "",
    session_handle: "",
    backend: "codex",
    model: "gpt-5.5",
    status: "running",
    runner_id: "r-hex1",
    delivered: true,
    cancel_requested: false,
    verdict: "none",
    trace_blob: "",
    result_text: "",
    is_error: false,
    cost_usd: 0,
    input_tokens: 0,
    output_tokens: 0,
    prompt_versions: { resolve: "mock" },
    created_at: now - 600,
    started_at: now - 540,
    finished_at: 0,
  },
  {
    id: "t-r2",
    project_id: "p-beacon",
    workstream_id: "ws-issue-18",
    repo: "git@github.com:acme/beacon.git",
    branch: "hive/issue-18",
    fresh_branch: false,
    kind: "review",
    instructions: "Review the fix for issue #18 on branch hive/issue-18.",
    conversation_id: "",
    conversation_turn: "",
    session_handle: "",
    backend: "codex",
    model: "gpt-5.5",
    status: "done",
    runner_id: "r-hex1",
    delivered: true,
    cancel_requested: false,
    verdict: "accept",
    trace_blob: "traces/t-r2.jsonl",
    result_text:
      "Subject corrected to \"Welcome to Beacon\" and the snapshot test updated.\n\nREVIEW: ACCEPT",
    is_error: false,
    cost_usd: 0.21,
    input_tokens: 48000,
    output_tokens: 2200,
    prompt_versions: { review: "mock" },
    created_at: now - 86400 * 4 + 300,
    started_at: now - 86400 * 4 + 360,
    finished_at: now - 86400 * 4 + 720,
  },
];

function makeIntakeTask(
  project: Project,
  conversation: AgentConversation,
  turn: string,
  status: Task["status"],
  message: string,
): Task {
  const started = status === "pending" ? 0 : Date.now() / 1000;
  const finished = status === "done" || status === "failed" || status === "cancelled" ? Date.now() / 1000 : 0;
  const task: Task = {
    id: `t-intake-${Math.random().toString(36).slice(2, 8)}`,
    project_id: project.id,
    workstream_id: "",
    repo: conversation.repo,
    branch: "",
    fresh_branch: false,
    kind: "intake",
    instructions: message || `Intake ${turn} turn`,
    conversation_id: conversation.id,
    conversation_turn: turn,
    session_handle: conversation.session_handle,
    backend: conversation.backend,
    model: conversation.model,
    status,
    runner_id: status === "pending" ? "" : "r-hex1",
    delivered: status !== "pending",
    cancel_requested: false,
    verdict: "none",
    trace_blob: status === "done" ? `traces/${conversation.id}-${turn}.jsonl` : "",
    result_text:
      status === "done"
        ? "## Intake brief\n\nMission, next iteration, and next steps updated from the latest scout turn."
        : "",
    is_error: status === "failed",
    cost_usd: status === "done" ? 0.18 : 0,
    input_tokens: status === "done" ? 24000 : 0,
    output_tokens: status === "done" ? 1800 : 0,
    prompt_versions: { intake: "mock" },
    created_at: Date.now() / 1000,
    started_at: started,
    finished_at: finished,
  };
  tasks.unshift(task);
  return task;
}

// `cards` and `subscription_candidates` are derived per request in resources().
const resourcesPayload: Omit<ResourcesPayload, "cards" | "subscription_candidates"> = {
  machines: [
    {
      id: "m-hex1",
      workspace_id: "default",
      name: "hex-1",
      hostname: "hex-1",
      kind: "runner",
      machine_type: "linux",
      os: "linux",
      arch: "x86_64",
      device_kind: "server",
      first_seen: now - 86400 * 6,
      last_seen: now - 12,
    },
    {
      id: "m-hex2",
      workspace_id: "default",
      name: "hex-2",
      hostname: "hex-2",
      kind: "runner",
      machine_type: "macbook",
      os: "macos",
      arch: "arm64",
      device_kind: "laptop",
      owner_user_id: "github:ikamensh",
      first_seen: now - 86400 * 4,
      last_seen: now - 60 * 47,
    },
  ],
  runners: [
    { id: "r-hex1", workspace_id: "default", machine_id: "m-hex1", name: "hex-1", backends: ["claude", "codex"], last_seen: now - 12, online: true },
    { id: "r-hex2", workspace_id: "default", machine_id: "m-hex2", name: "hex-2", backends: ["cursor", "gemini-cli"], last_seen: now - 60 * 47, online: false },
  ],
  resources: [
    { id: "res-1", runner_id: "r-hex1", backend: "claude", discovery_status: "ok", discovery_text: "", discovered_at: now - 120, cli_path: "/usr/local/bin/claude", cli_version: "1.0.0", usability_status: "usable", last_probe_at: now - 3600, last_probe_task_id: "probe-1", last_probe_text: "HIVE PROBE PASSED", cooldown_until: 0, last_exhaustion_at: 0, last_exhaustion_text: "", last_exhaustion_task_id: "", total_cost_usd: 214.6, total_tasks: 131, available: true, enabled: true, disabled_reason: "" },
    { id: "res-2", runner_id: "r-hex1", backend: "codex", discovery_status: "ok", discovery_text: "", discovered_at: now - 120, cli_path: "/usr/local/bin/codex", cli_version: "1.0.0", usability_status: "usable", last_probe_at: now - 7200, last_probe_task_id: "probe-2", last_probe_text: "HIVE PROBE PASSED", cooldown_until: now + 2700, last_exhaustion_at: now - 900, last_exhaustion_text: "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 3:28 PM.", last_exhaustion_task_id: "task-codex-quota", total_cost_usd: 88.1, total_tasks: 64, available: false, enabled: true, disabled_reason: "" },
    { id: "res-3", runner_id: "r-hex2", backend: "cursor", discovery_status: "warning", discovery_text: "authentication issue detected by preflight", discovered_at: now - 80, cli_path: "/usr/local/bin/cursor-agent", cli_version: "1.0.0", usability_status: "failed", last_probe_at: now - 900, last_probe_task_id: "probe-3", last_probe_text: "not authenticated", cooldown_until: now + 1860, last_exhaustion_at: 0, last_exhaustion_text: "", last_exhaustion_task_id: "", total_cost_usd: 41.9, total_tasks: 23, available: false, enabled: true, disabled_reason: "" },
    { id: "res-4", runner_id: "r-hex2", backend: "gemini-cli", discovery_status: "ok", discovery_text: "", discovered_at: now - 80, cli_path: "/usr/local/bin/gemini", cli_version: "1.0.0", usability_status: "usable", last_probe_at: now - 7200, last_probe_task_id: "probe-4", last_probe_text: "HIVE PROBE PASSED", cooldown_until: 0, last_exhaustion_at: 0, last_exhaustion_text: "", last_exhaustion_task_id: "", total_cost_usd: 3.2, total_tasks: 4, available: false, enabled: true, disabled_reason: "" },
  ],
  local_runner: {
    supported: true,
    running: false,
    registered: false,
    runner_name: "this-host",
    pid: 0,
    autostart: false,
    log_path: "/tmp/hive-data/local-runner.log",
    message: "",
  },
};

let orgContext =
  "We are Acme Corp. Prefer boring technology, Postgres over anything fancier.\nAll services deploy to GCP europe-west4. Python backends, TypeScript frontends.";

// The real backend groups agents under machines (hive.control.capacity); the
// mock mirrors it, deriving cards from the flat lists so probe/enable mutations
// are reflected without keeping two copies in sync.
function groupCards(): MachineGroup[] {
  const machines = resourcesPayload.machines ?? [];
  const machineIds = new Set(machines.map((m) => m.id));
  const claimed = new Set<string>();
  const card = (
    machine: MachineGroup["machine"],
    runners: MachineGroup["runners"],
    resources: MachineGroup["resources"],
  ): MachineGroup => {
    resources.forEach((r) => claimed.add(r.id));
    return {
      machine,
      online: runners.some((r) => r.online),
      last_seen: Math.max(machine.last_seen, ...runners.map((r) => r.last_seen), 0),
      runners,
      resources,
    };
  };
  const cards = machines.map((machine) => {
    const runners = resourcesPayload.runners.filter((r) => r.machine_id === machine.id);
    const runnerIds = new Set(runners.map((r) => r.id));
    return card(
      machine,
      runners,
      resourcesPayload.resources.filter((res) => res.machine_id === machine.id || runnerIds.has(res.runner_id)),
    );
  });
  for (const runner of resourcesPayload.runners) {
    if (runner.machine_id && machineIds.has(runner.machine_id)) continue;
    cards.push(
      card(
        { id: `runner:${runner.id}`, workspace_id: "default", name: runner.name, hostname: runner.name, kind: "runner", machine_type: "", os: "", arch: "", device_kind: "unknown", first_seen: runner.last_seen, last_seen: runner.last_seen },
        [runner],
        resourcesPayload.resources.filter((res) => res.runner_id === runner.id),
      ),
    );
  }
  const orphans = resourcesPayload.resources.filter((res) => !claimed.has(res.id));
  if (orphans.length) {
    cards.push(
      card(
        { id: "unassigned-resources", workspace_id: "", name: "unassigned", hostname: "", kind: "unknown", machine_type: "", os: "", arch: "", device_kind: "unknown", first_seen: 0, last_seen: 0 },
        [],
        orphans,
      ),
    );
  }
  return cards;
}

export const api = {
  version: async (): Promise<VersionInfo> => version,
  me: async (): Promise<AuthInfo> => ({
    user: {
      id: "github:ikamensh",
      github_login: "ikamensh",
      display_name: "ikamensh",
      created_at: now - 86400,
      last_seen: Date.now() / 1000,
    },
    workspace: { id: "default", name: "ikamen", created_at: now - 86400 },
    role: "admin",
    auth_mode: "dev",
    storage: {
      backend: "file",
      store_path: "/tmp/hive-data/store",
      gcp_project: null,
      blob_backend: "local",
      blob_path: "/tmp/hive-data/blobs",
      gcs_bucket: null,
      counts: { projects: projects.length },
      export_available: false,
      fully_managed: false,
    },
    version,
  }),
  logout: async (): Promise<void> => {},

  overview: async (): Promise<Overview> => {
    const projectName = (id: string) => projects.find((p) => p.id === id)?.name ?? "";
    const openQuestions = questions
      .filter((q) => q.status === "open")
      .sort((a, b) => b.created_at - a.created_at)
      .map((q) => ({
        id: q.id,
        project_id: q.project_id,
        project_name: projectName(q.project_id),
        text: q.text,
        created_at: q.created_at,
      }));
    const openTodos = structuredClone(humanTodos)
      .filter((t) => t.status === "open")
      .map((t) => ({
        id: t.id,
        project_id: t.project_id,
        project_name: projectName(t.project_id),
        assignee_user_id: t.assignee_user_id,
        title: t.title,
        instructions: t.instructions,
        created_at: t.created_at,
      }));

    return structuredClone({
      projects: [
        { id: "p-relay", name: "relay", spec_repo: "git@github.com:acme/relay.git", state: "working", paused: false, created_at: now - 86400 * 5, daily_budget_usd: 40, spend_today: 8.2, counts: { active: 2, running: 1, questions: 0, blockers: 0, streams: 3 } },
        { id: "p-atlas", name: "atlas", spec_repo: "git@github.com:acme/atlas-spec.git", state: "needs_attention", paused: false, created_at: now - 86400 * 9, daily_budget_usd: 25, spend_today: 3.1, counts: { active: 1, running: 0, questions: 1, blockers: 1, streams: 4 } },
        { id: "p-probe", name: "probe", spec_repo: "git@github.com:acme/probe.git", state: "blocked_resources", paused: false, created_at: now - 86400 * 2, daily_budget_usd: 15, spend_today: 0, counts: { active: 0, running: 0, questions: 0, blockers: 0, streams: 1 } },
        { id: "p-beacon", name: "beacon", spec_repo: "git@github.com:acme/beacon.git", state: "needs_attention", paused: false, created_at: now - 86400, daily_budget_usd: 0, spend_today: 1.4, counts: { active: 1, running: 0, questions: 1, blockers: 0, streams: 2 } },
        { id: "p-ledger", name: "ledger", spec_repo: "git@github.com:acme/ledger-spec.git", state: "idle_goal_complete", paused: false, created_at: now - 86400 * 20, daily_budget_usd: 0, spend_today: 0, counts: { active: 0, running: 0, questions: 0, blockers: 0, streams: 2 } },
      ],
      capacity: {
        machines_total: 2,
        machines_online: 1,
        agents_total: 4,
        agents_ready: 1,
        machines: [
          {
            id: "m-hex1", name: "hex-1", hostname: "hex-1", kind: "runner", device_kind: "server", online: true, last_seen: now - 12,
            agents: [
              { id: "res-1", backend: "claude", status: "ready", available: true, cooldown_until: 0, runner_id: "r-hex1" },
              { id: "res-2", backend: "codex", status: "cooldown", available: false, cooldown_until: now + 2700, runner_id: "r-hex1" },
            ],
          },
          {
            id: "m-hex2", name: "hex-2", hostname: "hex-2", kind: "runner", device_kind: "laptop", online: false, last_seen: now - 60 * 47,
            agents: [
              { id: "res-3", backend: "cursor", status: "offline", available: false, cooldown_until: 0, runner_id: "r-hex2" },
              { id: "res-4", backend: "gemini-cli", status: "offline", available: false, cooldown_until: 0, runner_id: "r-hex2" },
            ],
          },
        ],
      },
      live_tasks: [
        { id: "t-relay-1", project_id: "p-relay", project_name: "relay", backend: "claude", model: "", kind: "work", started_at: now - 95, issue_number: 0 },
      ],
      attention: {
        count: openQuestions.length + openTodos.length,
        offers: [
          {
            project_id: "p-beacon",
            project_name: "beacon",
            workstream_id: "stream-beacon-testing",
            repo: "https://github.com/acme/beacon.git",
            state: "missing" as const,
            summary: "No acceptance stories yet.",
            offer: "Hive can draft user stories with acceptance criteria autonomously from the spec (mission, iteration, wiki) — run a story refresh.",
            action: "refresh" as const,
          },
        ],
        questions: openQuestions,
        human_todos: openTodos,
      },
      subscriptions: structuredClone(subscriptions),
      totals: {
        tasks_running: 1,
        agents_ready: 1,
        agents_total: 4,
        machines_online: 1,
        machines_total: 2,
        needs_you: openQuestions.length + openTodos.length,
        spend_today: 12.7,
        budget_today: 80,
      },
    });
  },

  projects: async (): Promise<Project[]> => structuredClone(projects),

  createProject: async (body: ProjectCreate): Promise<Project> => {
    const p: Project = {
      id: `p-${Math.random().toString(36).slice(2, 8)}`,
      name: body.name,
      spec_repo: "",
      member_repos: [],
      mode: "build",
      autonomy: "direct_push",
      guess_propensity: "sometimes",
      prod_deploys: false,
      ci_autofix: false,
      testing_auto: true,
      paused: false,
      archived: false,
      daily_budget_usd: 0,
      goal_complete: false,
      goal_complete_note: "",
      intake_conversation_id: "",
      state: "idle",
      created_at: Date.now() / 1000,
    };
    projects.push(p);
    return structuredClone(p);
  },

  startProject: async (id: string): Promise<Project> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    if (!project.spec_repo.trim()) throw new Error("spec_repo required");
    return structuredClone(project);
  },

  createProjectRepo: async (id: string, body: ProjectRepoCreate): Promise<{ project: Project; repo: GithubRepo }> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    const owner = "acme";
    const name = (body.name || project.name).trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-|-$/g, "");
    if (!name) throw new Error("repo name required");
    const repo: GithubRepo = {
      full_name: `${owner}/${name}`,
      ssh_url: `git@github.com:${owner}/${name}.git`,
      clone_url: `https://github.com/${owner}/${name}.git`,
      private: body.private ?? true,
      description: `${project.name} spec home`,
    };
    mockGithubRepos.unshift(repo);
    project.spec_repo = repo.ssh_url;
    project.member_repos = [repo.ssh_url];
    project.state = "intake";
    return structuredClone({ project, repo });
  },

  startIntake: async (id: string, backend = ""): Promise<AgentConversation> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    if (!project.spec_repo.trim()) throw new Error("spec_repo required");
    const active = ["open", "running", "finalizing"];
    const existing = conversations.find(
      (c) => c.id === project.intake_conversation_id && active.includes(c.status),
    );
    if (existing) return structuredClone(existing);
    const conversation: AgentConversation = {
      id: `conv-${Math.random().toString(36).slice(2, 8)}`,
      project_id: id,
      role: "intake",
      repo: project.spec_repo,
      backend: backend || "claude",
      model: backend === "codex" ? "gpt-5.5" : "opus",
      status: "running",
      session_handle: "",
      latest_brief: "",
      transcript: [],
      last_task_id: "",
      created_at: Date.now() / 1000,
      updated_at: Date.now() / 1000,
    };
    conversations.push(conversation);
    project.intake_conversation_id = conversation.id;
    project.state = "intake";
    const task = makeIntakeTask(project, conversation, "initial", "running", "Inspect the project and produce an intake brief.");
    conversation.last_task_id = task.id;
    return structuredClone(conversation);
  },

  writeMission: async (id: string, backend = ""): Promise<{ conversation: AgentConversation; task: Task }> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    if (!project.spec_repo.trim()) throw new Error("spec_repo required");
    let conversation = conversations.find((c) => c.id === project.intake_conversation_id && c.status === "open");
    if (!conversation) {
      conversation = {
        id: `conv-${Math.random().toString(36).slice(2, 8)}`,
        project_id: id,
        role: "intake",
        repo: project.spec_repo,
        backend: backend || "codex",
        model: backend === "claude" ? "opus" : "gpt-5.5",
        status: "open",
        session_handle: "",
        latest_brief: "",
        transcript: [],
        last_task_id: "",
        created_at: Date.now() / 1000,
        updated_at: Date.now() / 1000,
      };
      conversations.push(conversation);
      project.intake_conversation_id = conversation.id;
    }
    const task = makeIntakeTask(project, conversation, "write_mission", "running", "Write mission.md and iteration.md.");
    conversation.status = "running";
    conversation.last_task_id = task.id;
    project.state = "intake";
    return structuredClone({ conversation, task });
  },

  finalizeIntake: async (id: string): Promise<{ conversation: AgentConversation }> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    if (!project.spec_repo.trim()) throw new Error("spec_repo required");
    let conversation = conversations.find((c) => c.id === project.intake_conversation_id);
    if (!conversation) {
      conversation = {
        id: `conv-${Math.random().toString(36).slice(2, 8)}`,
        project_id: id,
        role: "intake",
        repo: project.spec_repo,
        backend: "manual",
        model: "",
        status: "done",
        session_handle: "",
        latest_brief: "Accepted durable spec files.",
        transcript: [{ role: "system", text: "Accepted durable spec files." }],
        last_task_id: "",
        created_at: Date.now() / 1000,
        updated_at: Date.now() / 1000,
      };
      conversations.push(conversation);
      project.intake_conversation_id = conversation.id;
    }
    conversation.status = "done";
    conversation.latest_brief = conversation.latest_brief || "Accepted durable spec files.";
    conversation.updated_at = Date.now() / 1000;
    project.state = "idle";
    return structuredClone({ conversation });
  },

  conversationMessage: async (
    id: string,
    body: IntakeMessage,
  ): Promise<{ conversation: AgentConversation; task?: Task }> => {
    const conversation = conversations.find((c) => c.id === id);
    if (!conversation) throw new Error("not found");
    const project = projects.find((p) => p.id === conversation.project_id);
    if (!project) throw new Error("project not found");
    const action = body.action || "message";
    if (action === "approve") {
      conversation.status = "done";
      project.state = "idle";
      return structuredClone({ conversation });
    }
    const turn = action === "proceed" ? "proceed" : "message";
    const task = makeIntakeTask(project, conversation, turn, "done", body.message || "");
    conversation.status = "open";
    conversation.session_handle = conversation.session_handle || "mock-intake-session";
    conversation.latest_brief = `${conversation.latest_brief || "## Intake brief"}\n\n${body.message ? `User said: ${body.message}` : "Proceeding with stated assumptions."}`;
    conversation.transcript.push({ role: "user", text: body.message || action });
    conversation.transcript.push({ role: "assistant", text: conversation.latest_brief });
    conversation.last_task_id = task.id;
    conversation.updated_at = Date.now() / 1000;
    return structuredClone({ conversation, task });
  },

  project: async (id: string): Promise<ProjectDetail> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    // Mirrors hive._control.allowances.allowance_view: pretend 2 sessions ran
    // today against the first capped grant so the settings page shows headroom.
    const grants = project.agent_grants ?? [];
    const usedToday = grants.length > 0 ? 2 : 0;
    let charged = false;
    const allowanceGrants = grants.map((g) => {
      const charge = !charged && g.sessions_per_day !== null;
      if (charge) charged = true;
      return {
        ...g,
        remaining_today:
          g.sessions_per_day === null ? null : Math.max(g.sessions_per_day - (charge ? usedToday : 0), 0),
      };
    });
    const allowance = {
      limited: grants.length > 0,
      sessions_today: usedToday,
      grants: allowanceGrants,
      summary:
        allowanceGrants.length === 0
          ? "no limits"
          : allowanceGrants
              .map(
                (g) =>
                  `${g.backends.join(",") || "any backend"} × ${g.models.join(",") || "any model"}: ` +
                  (g.sessions_per_day === null ? "unlimited" : `${g.remaining_today}/${g.sessions_per_day} left today`),
              )
              .join("; "),
    };
    const testingHealth: Record<string, TestingHealth> =
      id === "p-beacon"
        ? {
            "stream-beacon-testing": {
              state: "failing",
              summary: "1 of 3 stories are failing; fixes flow through the issues workstream.",
              offer: "Run a testing episode after fixes land to confirm the stories go green.",
              action: "episode",
              counts: { active: 3, weak: 0, drafts: 1, untested: 1, stale: 0, failing: 1, blocked: 0, passing: 1 },
            },
          }
        : {};
    const testability: Record<string, TestabilityView> =
      id === "p-beacon"
        ? {
            "stream-beacon-testing": {
              contract: {
                id: "contract-beacon",
                workstream_id: "stream-beacon-testing",
                repo: "acme/beacon",
                content:
                  "# testability: beacon\n\n## Run\n### local\n    npm install\n    PORT=4310 npm run dev\n\n## Health\nGET http://localhost:4310/healthz returns 200 within 60s.\n\n## Reset\n`npm run seed` restores the demo dataset; state lives only in ./data.\n\n## Credentials & accounts\n- STRIPE_TEST_KEY — pending decision `stripe-sandbox`.\n\n## Constraints\n- Never touch production or real customer accounts.",
                fidelities: ["local"],
                status: "draft",
                probed_fidelity: "",
                probe_problems: [],
                probed_at: 0,
              },
              health: {
                state: "decisions",
                summary: "1 testability decision(s) need you — everything else is Hive's job.",
                offer: "Answer them below; Hive folds the answers into the contract and re-proves it.",
                action: "decide",
              },
              open_decisions: 1,
            },
          }
        : {};
    return structuredClone({
      project,
      testing_health: testingHealth,
      testability,
      workstreams: projectWorkstreams.filter((w) => w.project_id === id),
      work_items: workItems.filter((w) => w.project_id === id),
      tasks: tasks.filter((t) => t.project_id === id),
      questions: questions.filter((q) => q.project_id === id),
      human_todos: humanTodos.filter((t) => t.project_id === id),
      conversations: conversations.filter((c) => c.project_id === id),
      issue_runs: issueRuns.filter((r) => r.project_id === id),
      stories: stories.filter((s) => s.project_id === id),
      findings: findings.filter((f) => f.project_id === id),
      test_episodes: testEpisodes.filter((e) => e.project_id === id),
      directives: directives.filter((d) => d.project_id === id),
      checkouts: checkoutsForProject(id),
      allowance,
      decision_ledger: decisionLedgers[id] ?? {
        decisions: [],
        counts: { total: 0, operator_specified: 0, hive_assumed: 0, reopenable: 0 },
        source_types: [],
        must_ask: [
          "who is authorized to perform an action (permission/auth model)",
          "billing, pricing, or seat behavior",
          "data retention and destructive defaults (hard vs soft delete)",
          "public API contracts and breaking changes",
          "legal/compliance wording and notices",
          "security-sensitive defaults (e.g. token handling, account-existence leaks)",
        ],
        error: "",
      },
    });
  },

  reopenDecision: async (projectId: string, decisionId: string): Promise<unknown> => {
    const ledger = decisionLedgers[projectId];
    const decision = ledger?.decisions.find((d) => d.id === decisionId);
    if (!ledger || !decision || !decision.can_reopen) throw new Error("decision cannot be re-opened");
    decision.status = "needs_clarification";
    decision.can_reopen = false;
    ledger.counts.reopenable = ledger.decisions.filter((d) => d.can_reopen).length;
    const parked = workItems.filter(
      (w) => w.project_id === projectId && (w.source ?? "manual") === "manual" && w.status === "active",
    );
    for (const item of parked) {
      item.status = "parked";
      item.parked_reason = `decision ${decision.id} re-opened`;
    }
    const question: Question = {
      id: `q-decision-${decision.id.toLowerCase()}`,
      project_id: projectId,
      workstream_id: "",
      text: `## Re-open decision ${decision.id}: ${decision.title}\n\n${decision.body}`,
      status: "open",
      answer: "",
      created_at: Date.now() / 1000,
      answered_at: 0,
    };
    questions.unshift(question);
    return structuredClone({
      decision,
      question,
      parked_workstream_ids: parked.map((w) => w.id),
      commit: "mock",
    });
  },

  createDirective: async (id: string, text: string): Promise<Directive> => {
    const directive: Directive = {
      id: `dir-${Math.random().toString(36).slice(2, 8)}`,
      project_id: id,
      text: text.trim(),
      status: "working",
      issue_number: 42,
      issue_url: "https://github.com/acme/atlas/issues/42",
      routing_note: "filed issue #42; resolve task queued",
      created_at: Date.now() / 1000,
      updated_at: Date.now() / 1000,
    };
    directives.unshift(directive);
    return structuredClone(directive);
  },

  patchProject: async (id: string, patch: ProjectPatch): Promise<Project> => {
    const project = projects.find((p) => p.id === id)!;
    const { new_iteration_note, ...rest } = patch;
    Object.assign(project, rest);
    if (new_iteration_note !== undefined) {
      project.goal_complete = false;
      project.goal_complete_note = "";
    }
    return structuredClone(project);
  },

  updateWorkstream: async (projectId: string, workstreamId: string, patch: WorkstreamPatch): Promise<Workstream> => {
    const workstream = projectWorkstreams.find((w) => w.project_id === projectId && w.id === workstreamId);
    if (!workstream) throw new Error("not found");
    if (patch.title !== undefined) workstream.title = patch.title;
    if (patch.config !== undefined) workstream.config = patch.config;
    if (patch.enabled !== undefined) {
      workstream.enabled = patch.enabled;
      workstream.status = patch.enabled
        ? workstream.kind === "iteration" ? "active" : "idle"
        : "disabled";
    }
    workstream.updated_at = Date.now() / 1000;
    return structuredClone(workstream);
  },

  issuesPreflight: async (): Promise<PreflightResult> => ({
    ok: true,
    checks: [
      { name: "repo_set", ok: true, detail: "git@github.com:acme/beacon.git", hard: true },
      { name: "gh_token_present", ok: true, detail: "chief GitHub token present", hard: true },
      { name: "repo_write_access", ok: true, detail: "token can push/merge to acme/beacon", hard: true },
      { name: "codex_runner_usable", ok: true, detail: "an online runner offers a usable 'codex' resource", hard: false },
    ],
    runner_check_task: "task-preflight-mock",
  }),

  scanIssues: async (id: string): Promise<ScanResult> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    if (!project.spec_repo.trim()) throw new Error("spec_repo required");
    const open = workItems.filter(
      (w) => w.project_id === id && w.source === "issue" && w.status !== "done" && w.status !== "cancelled",
    );
    const run = {
      id: `run-${Date.now()}`,
      project_id: id,
      workstream_id: projectWorkstreams.find((w) => w.project_id === id && w.kind === "github_issues")?.id || "",
      repo: project.spec_repo,
      scope: "all_open_now" as const,
      issue_numbers: open.map((w) => w.issue_number || 0).filter(Boolean),
      status: "running" as const,
      counts: { open_issues: open.length },
      created_at: Date.now() / 1000,
      started_at: Date.now() / 1000,
      finished_at: 0,
    };
    issueRuns.push(run);
    return {
      open_issues: open.length,
      resolve_queued: open.filter((w) => w.status === "resolving").length,
      attachments_downloaded: 1,
      attachments_failed: 0,
      changes: [
        "re-gated #51 (blocked_clarity) → resolving",
        "re-gated #29 (rejected) → resolving",
        "no new open issues",
      ],
      run_id: run.id,
    };
  },

  workstreamPreflight: async (): Promise<PreflightResult> => ({
    ok: true,
    checks: [
      { name: "repo_set", ok: true, detail: "git@github.com:acme/beacon.git", hard: true },
      { name: "gh_token_present", ok: true, detail: "chief GitHub token present", hard: true },
      { name: "repo_write_access", ok: true, detail: "token can push/merge to acme/beacon", hard: true },
      { name: "codex_runner_usable", ok: true, detail: "an online runner offers a usable 'codex' resource", hard: false },
    ],
    runner_check_task: "task-preflight-mock",
  }),

  syncIssues: async (id: string): Promise<ScanResult> => {
    const open = workItems.filter(
      (w) => w.project_id === id && w.source === "issue" && w.status !== "done" && w.status !== "cancelled",
    );
    return {
      open_issues: open.length,
      resolve_queued: 0,
      attachments_downloaded: 0,
      attachments_failed: 0,
      changes: ["synced GitHub issues"],
    };
  },

  runIssues: async (
    id: string,
    workstreamId: string,
    body: { scope: "selected" | "all_open_now" | "scan_only"; issue_numbers?: number[] },
  ) => {
    const open = workItems.filter(
      (w) => w.project_id === id && w.source === "issue" && w.status !== "done" && w.status !== "cancelled",
    );
    const issueNumbers = body.scope === "selected"
      ? body.issue_numbers || []
      : open.map((w) => w.issue_number || 0).filter(Boolean);
    const run: IssueRun = {
      id: `run-${Date.now()}`,
      project_id: id,
      workstream_id: workstreamId,
      repo: projectWorkstreams.find((w) => w.id === workstreamId)?.repo || "",
      scope: body.scope,
      issue_numbers: issueNumbers,
      status: body.scope === "scan_only" ? "done" : "running",
      counts: { open_issues: open.length, queued: issueNumbers.length },
      created_at: Date.now() / 1000,
      started_at: body.scope === "scan_only" ? 0 : Date.now() / 1000,
      finished_at: body.scope === "scan_only" ? Date.now() / 1000 : 0,
    };
    issueRuns.push(run);
    return {
      run,
      open_issues: open.length,
      resolve_queued: body.scope === "scan_only" ? 0 : Math.min(1, issueNumbers.length),
      attachments_downloaded: 0,
      attachments_failed: 0,
      changes: [`started run ${run.id}`],
    };
  },

  cancelIssueRun: async (id: string): Promise<IssueRun> => {
    const run = issueRuns.find((r) => r.id === id);
    if (!run) throw new Error("not found");
    run.status = "cancelled";
    run.finished_at = Date.now() / 1000;
    run.counts = { ...run.counts, cancelled_tasks: 0 };
    return structuredClone(run);
  },

  checkCi: async (projectId: string, workstreamId: string): Promise<CiCheckResult> => {
    const stream = projectWorkstreams.find((w) => w.project_id === projectId && w.id === workstreamId);
    if (!stream) throw new Error("not found");
    return {
      repo: stream.repo,
      branch: "main",
      sha: "deadbee",
      conclusion: "passing",
      failing_checks: [],
      html_url: "",
      filed_issue: 0,
      filed_issue_url: "",
      already_filed: false,
      open_issues: 0,
      resolve_queued: 0,
    };
  },

  refreshTests: async (projectId: string, workstreamId: string): Promise<{ task: Task }> => {
    const stream = projectWorkstreams.find((w) => w.project_id === projectId && w.id === workstreamId);
    if (!stream) throw new Error("not found");
    const task: Task = {
      id: `task-test-refresh-${Date.now()}`,
      project_id: projectId,
      workstream_id: workstreamId,
      work_item_id: workstreamId,
      run_id: "",
      repo: stream.repo,
      branch: "",
      fresh_branch: false,
      kind: "test_refresh",
      instructions: "Refresh acceptance stories.",
      conversation_id: "",
      conversation_turn: "",
      session_handle: "",
      backend: "codex",
      model: "",
      status: "pending",
      runner_id: "",
      delivered: false,
      cancel_requested: false,
      verdict: "none",
      trace_blob: "",
      result_text: "",
      is_error: false,
      cost_usd: 0,
      input_tokens: 0,
      output_tokens: 0,
      prompt_versions: {},
      created_at: Date.now() / 1000,
      started_at: 0,
      finished_at: 0,
    };
    tasks.push(task);
    return structuredClone({ task });
  },

  runTests: async (
    projectId: string,
    workstreamId: string,
    body: { scope: "priority" | "full" | "selected"; story_keys?: string[]; max_stories?: number },
  ): Promise<TestEpisodeResult> => {
    const stream = projectWorkstreams.find((w) => w.project_id === projectId && w.id === workstreamId);
    if (!stream) throw new Error("not found");
    const selected = body.scope === "selected"
      ? body.story_keys || []
      : stories.filter((s) => s.workstream_id === workstreamId).slice(0, body.max_stories || 5).map((s) => s.key);
    const episode: TestEpisode = {
      id: `test-episode-${Date.now()}`,
      project_id: projectId,
      workstream_id: workstreamId,
      repo: stream.repo,
      scope: body.scope,
      story_keys: [],
      selected_story_keys: selected,
      max_stories: body.max_stories || 0,
      status: "refreshing",
      refresh_backend: "codex",
      refresh_model: "",
      sweep_backend: "codex",
      sweep_model: "",
      confirm_backend: "codex",
      confirm_model: "",
      counts: { stories_selected: selected.length },
      created_at: Date.now() / 1000,
      started_at: Date.now() / 1000,
      finished_at: 0,
    };
    testEpisodes.push(episode);
    const { task } = await api.refreshTests(projectId, workstreamId);
    task.run_id = episode.id;
    return structuredClone({ episode, refresh_task: task });
  },

  cancelTestEpisode: async (id: string): Promise<TestEpisode> => {
    const episode = testEpisodes.find((e) => e.id === id);
    if (!episode) throw new Error("not found");
    episode.status = "cancelled";
    episode.finished_at = Date.now() / 1000;
    return structuredClone(episode);
  },

  draftTestability: async (projectId: string, workstreamId: string): Promise<{ task: Task }> => {
    const { task } = await api.refreshTests(projectId, workstreamId);
    task.kind = "testability_draft";
    task.instructions = "Explore the repo and draft testability.md.";
    return structuredClone({ task });
  },

  probeTestability: async (projectId: string, workstreamId: string): Promise<{ task: Task }> => {
    const { task } = await api.refreshTests(projectId, workstreamId);
    task.kind = "testability_probe";
    task.instructions = "Stand the app up per testability.md and report.";
    return structuredClone({ task });
  },

  answerQuestion: async (id: string, answer: string): Promise<Question> => {
    const q = questions.find((x) => x.id === id)!;
    q.status = "answered";
    q.answer = answer;
    q.answered_at = Date.now() / 1000;
    return structuredClone(q);
  },

  feedback: async (): Promise<void> => {},

  task: async (id: string): Promise<Task> => structuredClone(tasks.find((t) => t.id === id)!),

  cancelTask: async (id: string): Promise<Task> => {
    const task = tasks.find((t) => t.id === id)!;
    if (task.status === "pending") {
      task.status = "cancelled";
      task.result_text = "Cancelled by operator before dispatch.";
      task.finished_at = Date.now() / 1000;
    } else if (task.status === "running") {
      task.cancel_requested = true;
    }
    return structuredClone(task);
  },

  trace: async (id: string): Promise<string> => {
    const task = tasks.find((t) => t.id === id);
    if (!task?.trace_blob) throw new Error("not found");
    return [
      JSON.stringify({ event: "run_init", agent_name: task.kind, backend: task.backend }),
      JSON.stringify({ event: "assistant_message", text: "I inspected the target code path." }),
      JSON.stringify({ event: "command", cmd: "pytest tests/auth -q", exit_code: 0 }),
      JSON.stringify({ event: "agent_run_end", cost_usd: task.cost_usd }),
    ].join("\n");
  },

  resources: async (): Promise<ResourcesPayload> => ({
    ...structuredClone(resourcesPayload),
    cards: structuredClone(groupCards()),
    subscription_candidates: subscriptionCandidates(),
  }),
  startLocalRunner: async () => {
    const local = resourcesPayload.local_runner!;
    local.running = true;
    local.registered = true;
    local.pid = 4242;
    local.message = "local runner starting";
    if (!resourcesPayload.runners.some((r) => r.name === local.runner_name)) {
      resourcesPayload.runners.unshift({
        id: "r-local",
        workspace_id: "default",
        machine_id: "m-local",
        name: local.runner_name,
        backends: ["codex"],
        last_seen: Date.now() / 1000,
        online: true,
      });
    }
    if (!resourcesPayload.machines?.some((m) => m.id === "m-local")) {
      resourcesPayload.machines?.unshift({
        id: "m-local",
        workspace_id: "default",
        name: local.runner_name,
        hostname: local.runner_name,
        kind: "runner",
        machine_type: "macbook",
        os: "macos",
        arch: "arm64",
        device_kind: "laptop",
        first_seen: Date.now() / 1000,
        last_seen: Date.now() / 1000,
      });
    }
    return structuredClone(local);
  },
  updateLocalRunner: async (patch: { autostart: boolean }) => {
    const local = resourcesPayload.local_runner!;
    local.autostart = patch.autostart;
    local.message = "local runner autostart updated";
    if (patch.autostart && !local.running) {
      return api.startLocalRunner();
    }
    return structuredClone(local);
  },
  probeResource: async (id: string) => {
    const res = resourcesPayload.resources.find((r) => r.id === id);
    if (!res) throw new Error("not found");
    res.usability_status = "probing";
    res.last_probe_at = Date.now() / 1000;
    res.last_probe_task_id = `probe-${Math.random().toString(36).slice(2, 8)}`;
    res.last_probe_text = "Probe queued.";
    res.usability_status = "usable";
    res.cooldown_until = 0;
    res.last_exhaustion_at = 0;
    res.last_exhaustion_text = "";
    res.last_exhaustion_task_id = "";
    res.available = res.enabled !== false && res.usability_status === "usable";
    res.total_tasks += 1;
    res.last_probe_text = "HIVE PROBE PASSED";
    return structuredClone({ resource: res });
  },
  updateResource: async (id: string, patch: { enabled?: boolean; disabled_reason?: string }) => {
    const res = resourcesPayload.resources.find((r) => r.id === id);
    if (!res) throw new Error("not found");
    if (patch.enabled !== undefined) {
      res.enabled = patch.enabled;
      res.disabled_reason = patch.enabled ? "" : (patch.disabled_reason || "Disabled by operator.");
      res.available = patch.enabled && res.usability_status === "usable" && res.cooldown_until <= Date.now() / 1000;
    }
    return structuredClone(res);
  },

  forgetMachine: async (id: string): Promise<void> => {
    const runnerIds = new Set(
      resourcesPayload.runners.filter((r) => r.machine_id === id).map((r) => r.id),
    );
    resourcesPayload.machines = (resourcesPayload.machines ?? []).filter((m) => m.id !== id);
    resourcesPayload.runners = resourcesPayload.runners.filter((r) => r.machine_id !== id);
    resourcesPayload.resources = resourcesPayload.resources.filter(
      (r) => r.machine_id !== id && !runnerIds.has(r.runner_id),
    );
  },

  createEnrollToken: async () => ({
    token: "mock-enroll-token",
    expires_in_s: 3600,
    command: "uv run hive enroll --url https://hive.example.com --token mock-enroll-token",
  }),

  setMachineOwner: async (id: string, owner_user_id: string) => {
    const machine = (resourcesPayload.machines ?? []).find((m) => m.id === id);
    if (machine) machine.owner_user_id = owner_user_id;
    return structuredClone(machine ?? {});
  },

  users: async (): Promise<WorkspaceMember[]> =>
    structuredClone(
      workspaceMembers.map((member) => ({
        ...member,
        machines: (resourcesPayload.machines ?? [])
          .filter((m) => m.owner_user_id === member.user.id)
          .map((m) => ({ id: m.id, name: m.name, device_kind: m.device_kind })),
        subscriptions: subscriptions
          .filter((s) => s.owner_user_id === member.user.id)
          .map((s) => ({ id: s.id, provider: s.provider, plan: s.plan })),
        open_todos: humanTodos.filter(
          (t) => t.status === "open" && t.assignee_user_id === member.user.id,
        ).length,
      })),
    ),

  setUserRole: async (userId: string, role: WorkspaceRole) => {
    const member = workspaceMembers.find((m) => m.user.id === userId);
    if (member) member.role = role;
    return { user_id: userId, role };
  },

  subscriptions: async (): Promise<Subscription[]> => structuredClone(subscriptions),

  addSubscription: async (
    provider: string,
    plan: string,
    licensing_mode: LicensingMode,
    notes: string,
  ): Promise<Subscription> => {
    const s: Subscription = {
      id: `s-${Math.random().toString(36).slice(2, 8)}`,
      provider,
      plan,
      licensing_mode,
      notes,
      created_at: Date.now() / 1000,
    };
    subscriptions.push(s);
    return structuredClone(s);
  },

  deleteSubscription: async (id: string): Promise<void> => {
    const i = subscriptions.findIndex((s) => s.id === id);
    if (i >= 0) subscriptions.splice(i, 1);
  },

  humanTodos: async (): Promise<HumanTodo[]> => structuredClone(humanTodos),

  completeHumanTodo: async (id: string): Promise<HumanTodo> => {
    const t = humanTodos.find((x) => x.id === id)!;
    t.status = "done";
    t.done_at = Date.now() / 1000;
    return structuredClone(t);
  },

  orgContext: async (): Promise<string> => orgContext,

  setOrgContext: async (text: string): Promise<void> => {
    orgContext = text;
  },

  githubRepos: async (): Promise<GithubRepo[]> => structuredClone(mockGithubRepos),

  validateGithubRepo: async (ref: string): Promise<GithubRepo> => {
    const key = ref.trim().toLowerCase().replace(/\.git$/, "");
    const hit = mockGithubRepos.find(
      (repo) =>
        repo.full_name.toLowerCase() === key ||
        repo.ssh_url.toLowerCase().includes(key) ||
        repo.full_name.toLowerCase().includes(key),
    );
    if (hit) return structuredClone(hit);
    throw Object.assign(new Error(`repo not found: ${ref}`), { status: 404 });
  },

  storage: async () => ({
    backend: "file" as const,
    store_path: "/tmp/hive-data/store",
    gcp_project: null,
    blob_backend: "local" as const,
    blob_path: "/tmp/hive-data/blobs",
    gcs_bucket: null,
    counts: { projects: projects.length },
    export_available: false,
    fully_managed: false,
  }),
};

const subscriptions: Subscription[] = [
  { id: "s1", provider: "codex", plan: "ChatGPT Plus", licensing_mode: "machine_bound", notes: "logged in on laptop", owner_user_id: "github:ikamensh", created_at: now - 86400 },
  { id: "s2", provider: "claude", plan: "Claude Max 5x", licensing_mode: "machine_bound", notes: "", owner_user_id: "github:ikamensh", created_at: now - 86400 },
];

// Provider-rulebook licensing defaults; mirrors hive/runner/backends.py.
const BACKEND_LICENSING: Record<string, LicensingMode> = {
  claude: "machine_bound",
  codex: "machine_bound",
  cursor: "portable",
  "gemini-cli": "portable",
};

// Mirror hive.control.capacity.subscription_candidates: providers proven usable
// on a machine but not yet recorded as a subscription.
function subscriptionCandidates(): SubscriptionCandidate[] {
  const have = new Set(subscriptions.map((s) => s.provider));
  const seen = new Map<string, SubscriptionCandidate>();
  for (const res of resourcesPayload.resources) {
    if (have.has(res.backend) || seen.has(res.backend)) continue;
    if (res.usability_status !== "usable") continue;
    const runner = resourcesPayload.runners.find((r) => r.id === res.runner_id);
    seen.set(res.backend, {
      provider: res.backend,
      licensing_mode: BACKEND_LICENSING[res.backend] ?? "unknown",
      evidence: `usable on ${runner?.name ?? res.runner_id}`,
    });
  }
  return [...seen.values()];
}

const workspaceMembers: WorkspaceMember[] = [
  {
    user: {
      id: "github:ikamensh",
      github_login: "ikamensh",
      display_name: "ikamensh",
      created_at: now - 86400 * 30,
      last_seen: now - 60,
    },
    role: "admin",
    is_you: true,
    machines: [],
    subscriptions: [],
    open_todos: 0,
  },
  {
    user: {
      id: "github:teammate",
      github_login: "teammate",
      display_name: "Teammate",
      created_at: now - 86400 * 7,
      last_seen: now - 7200,
    },
    role: "resource_provider",
    is_you: false,
    machines: [],
    subscriptions: [],
    open_todos: 0,
  },
];

const humanTodos: HumanTodo[] = [
  {
    id: "ht1",
    project_id: "",
    assignee_user_id: "github:ikamensh",
    title: "Log in codex on hive-vm",
    instructions:
      "Run on your laptop:\n\n```\ngcloud compute ssh hive-vm -- -L 1455:localhost:1455\nsudo HOME=/root codex login\n```\n\nOpen the printed URL in your local browser.",
    kind: "access",
    dedup_key: "access:codex:hive-vm",
    resolution: { check: "resource_usable", backend: "codex", runner_name: "hive-vm" },
    resolved_reason: "",
    status: "open",
    created_at: now - 3600,
    done_at: 0,
  },
  {
    id: "ht5",
    project_id: "",
    assignee_user_id: "github:teammate",
    title: "Fix cursor login on hex-1",
    instructions:
      "Refresh or repair the `cursor` CLI login on runner `hex-1`, then rerun the resource probe.",
    kind: "access",
    dedup_key: "access:cursor:hex-1",
    resolution: { check: "resource_usable", backend: "cursor", runner_name: "hex-1" },
    resolved_reason: "",
    status: "open",
    created_at: now - 2400,
    done_at: 0,
  },
  {
    id: "ht2",
    project_id: "p-probe",
    title: "Fix Hive orchestrator for probe",
    instructions:
      "The supervisor tried to wake the LLM orchestrator, but the invocation failed before it could plan work.\n\n```\nValueError: No API key was provided\n```",
    kind: "repair",
    dedup_key: "repair:orchestrator:p-probe",
    resolution: { check: "orchestrator_ran", project_id: "p-probe" },
    resolved_reason: "",
    status: "open",
    created_at: now - 1200,
    done_at: 0,
  },
  {
    id: "ht3",
    project_id: "",
    title: "Add GoDaddy A record: hive.example.com → 34.62.0.1",
    instructions: "In GoDaddy → DNS → Add record: type A, name hive, value 34.62.0.1.",
    kind: "external",
    dedup_key: "",
    resolution: {},
    resolved_reason: "",
    status: "open",
    created_at: now - 7200,
    done_at: 0,
  },
  {
    id: "ht4",
    project_id: "",
    title: "Fix claude login on raven",
    instructions: "Refresh the claude CLI login on runner raven.",
    kind: "access",
    dedup_key: "access:claude:raven",
    resolution: { check: "resource_usable", backend: "claude", runner_name: "raven" },
    resolved_reason: "`claude` probed usable on `raven`",
    status: "done",
    created_at: now - 90000,
    done_at: now - 86000,
  },
];
