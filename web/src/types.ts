// Mirrors hive/models.py (pydantic model_dump shapes).

export type Mode = "build" | "maintain";
export type Autonomy = "pr" | "direct_push";
export type GuessPropensity = "never" | "rarely" | "sometimes" | "often" | "always";
export type ProjectState =
  | "working"
  | "blocked_questions"
  | "blocked_resources"
  | "blocked_budget"
  | "idle_goal_complete"
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
  daily_budget_usd: number;
  goal_complete: boolean;
  goal_complete_note: string;
  state: ProjectState;
  created_at: number;
}

export interface Workstream {
  id: string;
  workspace_id?: string;
  project_id: string;
  title: string;
  description: string;
  status: "active" | "parked" | "done";
  parked_reason: string;
  created_at: number;
}

export interface Task {
  id: string;
  workspace_id?: string;
  project_id: string;
  workstream_id: string;
  repo: string;
  branch: string;
  kind: "work" | "verify" | "probe";
  instructions: string;
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
  tasks: Task[];
  questions: Question[];
  human_tasks: HumanTask[];
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

export interface ProjectPatch {
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
