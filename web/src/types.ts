// Mirrors hive/models.py (pydantic model_dump shapes).

export type Mode = "build" | "maintain";
export type Autonomy = "pr" | "direct_push";
export type WorkSource = "spec" | "issues";
export type GuessPropensity = "never" | "rarely" | "sometimes" | "often" | "always";
export type ProjectState =
  | "intake"
  | "working"
  | "blocked_questions"
  | "blocked_resources"
  | "blocked_budget"
  | "blocked_clarity"
  | "idle_goal_complete"
  | "idle_no_workstreams"
  | "idle_no_open_issues";

export interface Project {
  id: string;
  workspace_id?: string;
  name: string;
  spec_repo: string;
  member_repos: string[];
  mode: Mode;
  autonomy: Autonomy;
  work_source: WorkSource;
  guess_propensity: GuessPropensity;
  prod_deploys: boolean;
  paused: boolean;
  daily_budget_usd: number;
  goal_complete: boolean;
  goal_complete_note: string;
  intake_conversation_id: string;
  state: ProjectState;
  created_at: number;
}

export type WorkstreamKind = "iteration" | "github_issues";
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
  kind: "work" | "verify" | "probe" | "intake" | "resolve" | "review" | "preflight";
  instructions: string;
  conversation_id: string;
  conversation_turn: string;
  session_handle: string;
  backend: string;
  model: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  runner_id: string;
  delivered: boolean;
  cancel_requested: boolean;
  verdict: "none" | "accept" | "reject";
  trace_blob: string;
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
  human_tasks: HumanTask[];
  conversations: AgentConversation[];
  issue_runs: IssueRun[];
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
}

export interface StorageExportResult {
  gcp_project: string;
  gcs_bucket?: string | null;
  documents: Record<string, number>;
  blobs: number;
  message: string;
}

export interface RunnerInfo {
  id: string;
  workspace_id?: string;
  machine_id?: string;
  name: string;
  backends: string[];
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

export interface HumanTask {
  id: string;
  workspace_id?: string;
  project_id: string; // empty = org-wide
  title: string;
  instructions: string;
  status: "open" | "done";
  created_at: number;
  done_at: number;
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
  spec_repo?: string;
  mode?: Mode;
  autonomy?: Autonomy;
  work_source?: WorkSource;
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
