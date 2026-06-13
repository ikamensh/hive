// Canned fixtures served when VITE_MOCK=1. Mutations update in-memory state so
// the UI stays interactive for screenshots and offline development.

import type {
  HumanTask,
  Project,
  ProjectCreate,
  ProjectDetail,
  ProjectPatch,
  Question,
  ResourcesPayload,
  Subscription,
  Task,
  Workstream,
} from "./types";

const now = Date.now() / 1000;

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
    guess_propensity: "never",
    prod_deploys: false,
    paused: true,
    goal_complete: false,
    goal_complete_note: "",
    daily_budget_usd: 0,
    state: "blocked_resources",
    created_at: now - 3600 * 5,
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
];

const resourcesPayload: ResourcesPayload = {
  runners: [
    { id: "r-hex1", name: "hex-1", backends: ["claude", "codex"], last_seen: now - 12, online: true },
    { id: "r-hex2", name: "hex-2", backends: ["cursor", "gemini-cli"], last_seen: now - 60 * 47, online: false },
  ],
  resources: [
    { id: "res-1", runner_id: "r-hex1", backend: "claude", usability_status: "usable", last_probe_at: now - 3600, last_probe_task_id: "probe-1", last_probe_text: "HIVE PROBE PASSED", cooldown_until: 0, total_cost_usd: 214.6, total_tasks: 131, available: true },
    { id: "res-2", runner_id: "r-hex1", backend: "codex", usability_status: "unknown", last_probe_at: 0, last_probe_task_id: "", last_probe_text: "", cooldown_until: 0, total_cost_usd: 88.1, total_tasks: 64, available: false },
    { id: "res-3", runner_id: "r-hex2", backend: "cursor", usability_status: "failed", last_probe_at: now - 900, last_probe_task_id: "probe-3", last_probe_text: "not authenticated", cooldown_until: now + 1860, total_cost_usd: 41.9, total_tasks: 23, available: false },
    { id: "res-4", runner_id: "r-hex2", backend: "gemini-cli", usability_status: "usable", last_probe_at: now - 7200, last_probe_task_id: "probe-4", last_probe_text: "HIVE PROBE PASSED", cooldown_until: 0, total_cost_usd: 3.2, total_tasks: 4, available: true },
  ],
};

let orgContext =
  "We are Acme Corp. Prefer boring technology, Postgres over anything fancier.\nAll services deploy to GCP europe-west4. Python backends, TypeScript frontends.";

export const api = {
  projects: async (): Promise<Project[]> => structuredClone(projects),

  createProject: async (body: ProjectCreate): Promise<Project> => {
    const p: Project = {
      id: `p-${Math.random().toString(36).slice(2, 8)}`,
      name: body.name,
      spec_repo: body.spec_repo,
      member_repos: body.member_repos,
      mode: body.mode,
      autonomy: body.autonomy,
      guess_propensity: body.guess_propensity,
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
  probeResource: async (id: string) => {
    const res = resourcesPayload.resources.find((r) => r.id === id);
    if (!res) throw new Error("not found");
    res.usability_status = "probing";
    res.last_probe_at = Date.now() / 1000;
    res.last_probe_task_id = `probe-${Math.random().toString(36).slice(2, 8)}`;
    res.last_probe_text = "Probe queued.";
    res.usability_status = "usable";
    res.available = res.cooldown_until <= Date.now() / 1000;
    res.total_tasks += 1;
    res.last_probe_text = "HIVE PROBE PASSED";
    return structuredClone({ resource: res });
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
