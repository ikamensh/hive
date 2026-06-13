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
  project_id: string;
  title: string;
  description: string;
  status: "active" | "parked" | "done";
  parked_reason: string;
  created_at: number;
}

export interface Task {
  id: string;
  project_id: string;
  workstream_id: string;
  repo: string;
  kind: "work" | "verify";
  instructions: string;
  backend: string;
  model: string;
  status: "pending" | "running" | "done" | "failed";
  runner_id: string;
  delivered: boolean;
  result_text: string;
  is_error: boolean;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  created_at: number;
  started_at: number;
  finished_at: number;
}

export interface Question {
  id: string;
  project_id: string;
  workstream_id: string;
  text: string;
  status: "open" | "answered";
  answer: string;
  created_at: number;
  answered_at: number;
}

export interface ProjectDetail {
  project: Project;
  workstreams: Workstream[];
  tasks: Task[];
  questions: Question[];
}

export interface RunnerInfo {
  id: string;
  name: string;
  backends: string[];
  last_seen: number;
  online: boolean;
}

export interface ResourceInfo {
  id: string;
  runner_id: string;
  backend: string;
  usability_status: "unknown" | "probing" | "usable" | "failed";
  last_probe_at: number;
  last_probe_task_id: string;
  last_probe_text: string;
  cooldown_until: number;
  total_cost_usd: number;
  total_tasks: number;
  available: boolean;
}

export interface ResourcesPayload {
  runners: RunnerInfo[];
  resources: ResourceInfo[];
}

export interface Subscription {
  id: string;
  provider: string;
  plan: string;
  notes: string;
  created_at: number;
}

export interface HumanTask {
  id: string;
  project_id: string; // empty = org-wide
  title: string;
  instructions: string;
  status: "open" | "done";
  created_at: number;
  done_at: number;
}

export interface ProjectCreate {
  name: string;
  spec_repo: string;
  member_repos: string[];
  mode: Mode;
  autonomy: Autonomy;
  guess_propensity: GuessPropensity;
}

export interface ProjectPatch {
  mode?: Mode;
  autonomy?: Autonomy;
  guess_propensity?: GuessPropensity;
  prod_deploys?: boolean;
  paused?: boolean;
  member_repos?: string[];
  new_iteration_note?: string;
}
