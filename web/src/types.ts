// Mirrors hive/models.py (pydantic model_dump shapes).

export type Mode = "build" | "maintain";
export type Autonomy = "pr" | "direct_push";
export type GuessPropensity = "never" | "rarely" | "sometimes" | "often" | "always";
export type ProjectState =
  | "intake"
  | "working"
  | "needs_attention"
  | "blocked_questions"
  | "blocked_resources"
  | "blocked_budget"
  | "blocked_clarity"
  | "idle_goal_complete"
  | "idle"
  | "idle_no_workstreams";

export interface Project {
  id: string;
  workspace_id?: string;
  name: string;
  spec_repo: string;
  member_repos: string[];
  mode: Mode;
  autonomy: Autonomy;
  guess_propensity: GuessPropensity;
  prod_deploys: boolean;
  paused: boolean;
  archived: boolean;
  daily_budget_usd: number;
  goal_complete: boolean;
  goal_complete_note: string;
  intake_conversation_id: string;
  state: ProjectState;
  created_at: number;
}

export type WorkstreamKind = "iteration" | "github_issues" | "testing";
export type ProjectWorkstreamStatus = "idle" | "active" | "blocked" | "disabled";

export interface Workstream {
  id: string;
  workspace_id?: string;
  project_id: string;
  kind: WorkstreamKind;
  title: string;
  repo: string;
  source_ref: Record<string, unknown>;
  status: ProjectWorkstreamStatus;
  enabled: boolean;
  config: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface WorkstreamPatch {
  title?: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
}

/** Iteration work items plus the issue-solving per-issue lifecycle. */
export type WorkItemStatus =
  | "active"
  | "queued"
  | "parked"
  | "done"
  | "resolving"
  | "blocked_clarity"
  | "reviewing"
  | "rejected"
  | "cancelled";

export interface WorkItem {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id?: string;
  repo?: string;
  title: string;
  description: string;
  status: WorkItemStatus;
  parked_reason: string;
  // Issue solving: each work item tracks one GitHub issue.
  source?: "manual" | "issue";
  issue_number?: number;
  issue_url?: string;
  order?: number;
  issue_attachments?: string[];
  external_ref?: Record<string, unknown>;
  created_at: number;
}

export interface Task {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id: string;
  work_item_id?: string;
  run_id?: string;
  repo: string;
  branch: string;
  fresh_branch: boolean;
  kind:
    | "work"
    | "verify"
    | "probe"
    | "intake"
    | "resolve"
    | "review"
    | "preflight"
    | "test_refresh"
    | "test_sweep"
    | "test_reproduce"
    | "test_judge";
  instructions: string;
  conversation_id: string;
  conversation_turn: string;
  session_handle: string;
  issue_number?: number;
  issue_doc?: string;
  issue_attachments?: string[];
  required_capabilities?: string[];
  backend: string;
  model: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  runner_id: string;
  delivered: boolean;
  cancel_requested: boolean;
  verdict: "none" | "accept" | "reject";
  trace_blob: string;
  artifact_blobs?: string[];
  result_text: string;
  is_error: boolean;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  prompt_versions: Record<string, string>;
  created_at: number;
  started_at: number;
  finished_at: number;
}

export interface AgentConversation {
  id: string;
  workspace_id?: string;
  project_id: string;
  role: "intake";
  repo: string;
  backend: string;
  model: string;
  status: "open" | "running" | "finalizing" | "done" | "failed";
  session_handle: string;
  latest_brief: string;
  transcript: { role: string; text: string }[];
  last_task_id: string;
  created_at: number;
  updated_at: number;
}

export interface Question {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id: string;
  text: string;
  status: "open" | "answered" | "dismissed";
  answer: string;
  created_at: number;
  answered_at: number;
}

export interface ProjectDetail {
  project: Project;
  workstreams: Workstream[];
  work_items: WorkItem[];
  tasks: Task[];
  questions: Question[];
  human_todos: HumanTodo[];
  human_tasks?: HumanTodo[];
  conversations: AgentConversation[];
  issue_runs: IssueRun[];
  stories: Story[];
  findings: Finding[];
  test_episodes: TestEpisode[];
}

export interface IssueRun {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id: string;
  repo: string;
  scope: "selected" | "all_open_now" | "scan_only";
  issue_numbers: number[];
  status: "scanning" | "queued" | "running" | "blocked" | "done" | "cancelled" | "failed";
  counts: Record<string, number>;
  created_at: number;
  started_at: number;
  finished_at: number;
}

export interface Story {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id: string;
  repo: string;
  key: string;
  title: string;
  intent: string;
  acceptance: string;
  spec_ref: string;
  tags: string[];
  status: "untested" | "passing" | "failing" | "blocked" | "stale" | "archived";
  centrality: "core" | "major" | "minor";
  centrality_locked: boolean;
  spec_baseline: string;
  blessed: boolean;
  blessed_at: number;
  last_tested_baseline: string;
  last_fidelity: "none" | "local" | "docker";
  open_issue_number: number;
  open_issue_url: string;
  known_limitations: string[];
  last_episode_id: string;
  last_result_task_id: string;
  last_tested_at: number;
  order: number;
  created_at: number;
  updated_at: number;
}

export interface Finding {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id: string;
  repo: string;
  episode_id: string;
  story_key: string;
  kind: "bug" | "ux_smell";
  severity: string;
  summary: string;
  expected: string;
  actual: string;
  detail: string;
  oracle: string;
  evidence_blobs: string[];
  status: "suspected" | "confirmed" | "blocked" | "rejected" | "constrained" | "duplicate";
  issue_number: number;
  issue_url: string;
  sweep_task_id: string;
  confirm_task_id: string;
  signature: string;
  created_at: number;
  updated_at: number;
}

export interface TestEpisode {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id: string;
  repo: string;
  scope: "priority" | "full" | "selected";
  story_keys: string[];
  selected_story_keys: string[];
  max_stories: number;
  status: "refreshing" | "sweeping" | "confirming" | "done" | "cancelled" | "failed";
  refresh_backend: string;
  refresh_model: string;
  sweep_backend: string;
  sweep_model: string;
  confirm_backend: string;
  confirm_model: string;
  counts: Record<string, number | string>;
  created_at: number;
  started_at: number;
  finished_at: number;
}

export interface GithubRepo {
  full_name: string;
  ssh_url: string;
  clone_url: string;
  private: boolean;
  description: string;
}

export interface HiveUser {
  id: string;
  github_login: string;
  display_name: string;
  created_at: number;
  last_seen: number;
}

export interface Workspace {
  id: string;
  name: string;
  created_at: number;
}

export interface AuthInfo {
  user: HiveUser;
  workspace: Workspace;
  auth_mode: string;
  storage?: StorageInfo;
}

export interface StorageInfo {
  backend: "firestore" | "file" | "memory";
  store_path?: string | null;
  gcp_project?: string | null;
  blob_backend: "gcs" | "local";
  blob_path?: string | null;
  gcs_bucket?: string | null;
  counts: Record<string, number>;
  export_available: boolean;
  fully_managed: boolean;
}

export interface RunnerInfo {
  id: string;
  workspace_id?: string;
  machine_id?: string;
  name: string;
  backends: string[];
  capabilities?: string[];
  last_seen: number;
  online: boolean;
}

export interface MachineInfo {
  id: string;
  workspace_id: string;
  name: string;
  hostname: string;
  kind: string;
  machine_type: string;
  os: string;
  arch: string;
  device_kind: string;
  first_seen: number;
  last_seen: number;
}

export interface ResourceInfo {
  id: string;
  workspace_id?: string;
  machine_id?: string;
  runner_id: string;
  backend: string;
  discovery_status: string;
  discovery_text: string;
  discovered_at: number;
  cli_path: string;
  cli_version: string;
  usability_status: "unknown" | "probing" | "usable" | "failed";
  last_probe_at: number;
  last_probe_task_id: string;
  last_probe_text: string;
  browser_status?: "unknown" | "probing" | "usable" | "failed";
  browser_probe_at?: number;
  browser_probe_text?: string;
  docker_status?: "unknown" | "probing" | "usable" | "failed";
  docker_probe_at?: number;
  docker_probe_text?: string;
  cooldown_until: number;
  last_exhaustion_at: number;
  last_exhaustion_text: string;
  last_exhaustion_task_id: string;
  total_cost_usd: number;
  total_tasks: number;
  available: boolean;
  enabled?: boolean;
  disabled_reason?: string;
}

export interface ResourcesPayload {
  machines?: MachineInfo[];
  runners: RunnerInfo[];
  resources: ResourceInfo[];
  local_runner?: LocalRunnerInfo;
}

export interface LocalRunnerInfo {
  supported: boolean;
  running: boolean;
  registered: boolean;
  runner_name: string;
  pid: number;
  autostart: boolean;
  log_path: string;
  message: string;
}

export interface Subscription {
  id: string;
  workspace_id?: string;
  provider: string;
  plan: string;
  notes: string;
  created_at: number;
}

export interface HumanTodo {
  id: string;
  workspace_id?: string;
  project_id: string; // empty = org-wide
  title: string;
  instructions: string;
  status: "open" | "done";
  created_at: number;
  done_at: number;
}

export type HumanTask = HumanTodo;

// ---- home dashboard (mirrors hive/control/overview.py) ---------------------

export interface OverviewProject {
  id: string;
  name: string;
  spec_repo: string;
  state: ProjectState;
  paused: boolean;
  created_at: number;
  daily_budget_usd: number;
  spend_today: number;
  counts: {
    active: number;
    running: number;
    questions: number;
    blockers: number;
    streams: number;
  };
}

export type AgentStatus =
  | "ready"
  | "cooldown"
  | "probing"
  | "probe"
  | "failed"
  | "offline"
  | "disabled";

export interface OverviewAgent {
  id: string;
  backend: string;
  status: AgentStatus;
  available: boolean;
  cooldown_until: number;
  runner_id: string;
}

export interface OverviewMachine {
  id: string;
  name: string;
  hostname: string;
  kind: string;
  device_kind: string;
  online: boolean;
  last_seen: number;
  agents: OverviewAgent[];
}

export interface OverviewCapacity {
  machines: OverviewMachine[];
  machines_total: number;
  machines_online: number;
  agents_total: number;
  agents_ready: number;
}

export interface OverviewLiveTask {
  id: string;
  project_id: string;
  project_name: string;
  backend: string;
  model: string;
  kind: Task["kind"];
  started_at: number;
  issue_number: number;
}

export interface OverviewQuestion {
  id: string;
  project_id: string;
  project_name: string;
  text: string;
  created_at: number;
}

export interface OverviewTodo {
  id: string;
  project_id: string;
  project_name: string;
  title: string;
  instructions: string;
  created_at: number;
}

export interface OverviewAttention {
  count: number;
  questions: OverviewQuestion[];
  human_todos: OverviewTodo[];
}

export interface OverviewTotals {
  tasks_running: number;
  agents_ready: number;
  agents_total: number;
  machines_online: number;
  machines_total: number;
  needs_you: number;
  spend_today: number;
  budget_today: number;
}

export interface Overview {
  projects: OverviewProject[];
  capacity: OverviewCapacity;
  live_tasks: OverviewLiveTask[];
  attention: OverviewAttention;
  subscriptions: Subscription[];
  totals: OverviewTotals;
}

export interface ProjectCreate {
  name: string;
}

export interface ProjectStart {
  mission?: string;
  iteration_goal?: string;
}

export interface IntakeMessage {
  action?: "message" | "proceed" | "approve";
  message?: string;
}

export interface ProjectRepoCreate {
  name?: string;
  private?: boolean;
}

export interface ProjectPatch {
  name?: string;
  archived?: boolean;
  spec_repo?: string;
  mode?: Mode;
  autonomy?: Autonomy;
  guess_propensity?: GuessPropensity;
  prod_deploys?: boolean;
  paused?: boolean;
  daily_budget_usd?: number;
  member_repos?: string[];
  new_iteration_note?: string;
}

export interface PreflightCheck {
  name: string;
  ok: boolean;
  detail: string;
  hard: boolean;
}

export interface PreflightResult {
  ok: boolean;
  checks: PreflightCheck[];
  runner_check_task?: string | null;
}

export interface ScanResult {
  open_issues: number;
  resolve_queued: number;
  attachments_downloaded: number;
  attachments_failed: number;
  changes: string[];
  run_id?: string;
}

export interface IssueRunResult extends ScanResult {
  run: IssueRun;
}

export interface TestEpisodeResult {
  episode: TestEpisode;
  refresh_task: Task;
}
