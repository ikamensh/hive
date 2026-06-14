// Canned fixtures served when VITE_MOCK=1. Mutations update in-memory state so
// the UI stays interactive for screenshots and offline development.

import type {
  AuthInfo,
  GithubRepo,
  HumanTask,
  Project,
  ProjectCreate,
  ProjectDetail,
  ProjectPatch,
  ProjectStart,
  Question,
  ResourcesPayload,
  ScanResult,
  Subscription,
  Task,
  Workstream,
} from "./types";

const now = Date.now() / 1000;

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
    description: "Beacon — issues-mode demo repo",
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
    work_source: "spec",
    guess_propensity: "sometimes",
    prod_deploys: false,
    paused: false,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
    state: "blocked_questions",
    created_at: now - 86400 * 6,
  },
  {
    id: "p-relay",
    name: "relay",
    spec_repo: "git@github.com:acme/relay.git",
    member_repos: ["git@github.com:acme/relay.git"],
    mode: "build",
    autonomy: "pr",
    work_source: "spec",
    guess_propensity: "often",
    prod_deploys: true,
    paused: false,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
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
    work_source: "spec",
    guess_propensity: "rarely",
    prod_deploys: true,
    paused: false,
    goal_complete: true,
    goal_complete_note:
      "All v1 invoicing flows shipped and verified. Remaining backlog is empty; test suite green across both repos.",
    daily_budget_usd: 0,
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
    work_source: "spec",
    guess_propensity: "never",
    prod_deploys: false,
    paused: true,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
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
    work_source: "issues",
    guess_propensity: "rarely",
    prod_deploys: false,
    paused: false,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
    state: "blocked_clarity",
    created_at: now - 86400 * 3,
  },
];

const workstreams: Workstream[] = [
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
    text: "Should EU customer data stay in `eu-west` exclusively, or is cross-region replication to `us-east` for DR acceptable? The spec's compliance section is silent on this.",
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
];

const tasks: Task[] = [
  {
    id: "t-1",
    project_id: "p-atlas",
    workstream_id: "ws-auth",
    repo: "git@github.com:acme/atlas-api.git",
    branch: "",
    kind: "work",
    instructions: "Implement refresh token rotation per spec §4.2",
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
    kind: "verify",
    instructions: "Verify refresh rotation against spec §4.2",
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
    kind: "work",
    instructions: "Fix wizard step 3 validation",
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
    kind: "resolve",
    instructions: "Clarify then fix issue #42 (login redirect drops `next`). See .hive/issue-42/ISSUE.md.",
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
    kind: "review",
    instructions: "Review the fix for issue #18 on branch hive/issue-18.",
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

const resourcesPayload: ResourcesPayload = {
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

export const api = {
  me: async (): Promise<AuthInfo> => ({
    user: {
      id: "github:ikamensh",
      github_login: "ikamensh",
      display_name: "ikamensh",
      created_at: now - 86400,
      last_seen: Date.now() / 1000,
    },
    workspace: { id: "default", name: "ikamen", created_at: now - 86400 },
    auth_mode: "dev",
    storage: {
      backend: "file",
      store_path: "/tmp/hive-data/store",
      gcp_project: null,
      blob_backend: "local",
      blob_path: "/tmp/hive-data/blobs",
      gcs_bucket: null,
      counts: { projects: projects.length },
      export_available: true,
    },
  }),
  logout: async (): Promise<void> => {},

  projects: async (): Promise<Project[]> => structuredClone(projects),

  createProject: async (body: ProjectCreate): Promise<Project> => {
    const p: Project = {
      id: `p-${Math.random().toString(36).slice(2, 8)}`,
      name: body.name,
      spec_repo: "",
      member_repos: [],
      mode: "build",
      autonomy: "direct_push",
      work_source: "spec",
      guess_propensity: "sometimes",
      prod_deploys: false,
      paused: false,
      daily_budget_usd: 0,
      goal_complete: false,
      goal_complete_note: "",
      state: "idle_no_workstreams",
      created_at: Date.now() / 1000,
    };
    projects.push(p);
    return structuredClone(p);
  },

  startProject: async (id: string, _body: ProjectStart): Promise<Project> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    if (!project.spec_repo.trim()) throw new Error("spec_repo required");
    return structuredClone(project);
  },

  project: async (id: string): Promise<ProjectDetail> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    return structuredClone({
      project,
      workstreams: workstreams.filter((w) => w.project_id === id),
      tasks: tasks.filter((t) => t.project_id === id),
      questions: questions.filter((q) => q.project_id === id),
      human_tasks: humanTasks.filter((t) => t.project_id === id),
    });
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

  scanIssues: async (id: string): Promise<ScanResult> => {
    const project = projects.find((p) => p.id === id);
    if (!project) throw new Error("not found");
    if (project.work_source !== "issues") throw new Error("project is not in issues mode");
    if (!project.spec_repo.trim()) throw new Error("spec_repo required");
    const open = workstreams.filter(
      (w) => w.project_id === id && w.source === "issue" && w.status !== "done" && w.status !== "cancelled",
    );
    return {
      open_issues: open.length,
      resolve_queued: open.filter((w) => w.status === "resolving").length,
      changes: [
        "re-gated #51 (blocked_clarity) → resolving",
        "re-gated #29 (rejected) → resolving",
        "no new open issues",
      ],
    };
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

  resources: async (): Promise<ResourcesPayload> => structuredClone(resourcesPayload),
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

  subscriptions: async (): Promise<Subscription[]> => structuredClone(subscriptions),

  addSubscription: async (provider: string, plan: string, notes: string): Promise<Subscription> => {
    const s: Subscription = {
      id: `s-${Math.random().toString(36).slice(2, 8)}`,
      provider,
      plan,
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

  humanTasks: async (): Promise<HumanTask[]> => structuredClone(humanTasks),

  completeHumanTask: async (id: string): Promise<HumanTask> => {
    const t = humanTasks.find((x) => x.id === id)!;
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
    export_available: true,
  }),

  exportStorage: async () => ({
    gcp_project: "hive-ikamen",
    gcs_bucket: null,
    documents: { projects: projects.length },
    blobs: 0,
    message: "Exported to Firestore project 'hive-ikamen'. Restart with HIVE_GCP_PROJECT='hive-ikamen' to use the cloud store.",
  }),
};

const subscriptions: Subscription[] = [
  { id: "s1", provider: "codex", plan: "ChatGPT Plus", notes: "logged in on laptop", created_at: now - 86400 },
  { id: "s2", provider: "claude", plan: "Claude Max 5x", notes: "", created_at: now - 86400 },
];

const humanTasks: HumanTask[] = [
  {
    id: "ht1",
    project_id: "",
    title: "Log in codex on hive-vm",
    instructions:
      "Run on your laptop:\n\n```\ngcloud compute ssh hive-vm -- -L 1455:localhost:1455\nsudo HOME=/root codex login\n```\n\nOpen the printed URL in your local browser.",
    status: "open",
    created_at: now - 3600,
    done_at: 0,
  },
  {
    id: "ht2",
    project_id: "p-probe",
    title: "Fix Hive orchestrator for probe",
    instructions:
      "The supervisor tried to wake the LLM orchestrator, but the invocation failed before it could plan work.\n\n```\nValueError: No API key was provided\n```",
    status: "open",
    created_at: now - 1200,
    done_at: 0,
  },
];
