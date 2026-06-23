import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AuthInfo,
  AgentConversation,
  CiCheckResult,
  Directive,
  HumanTodo,
  GithubRepo,
  IntakeMessage,
  IssueRun,
  IssueRunResult,
  Overview,
  Project,
  ProjectCreate,
  ProjectDetail,
  ProjectPatch,
  ProjectRepoCreate,
  LicensingMode,
  ProjectStart,
  PreflightResult,
  Question,
  ResourcesPayload,
  ScanResult,
  StorageInfo,
  Subscription,
  Task,
  TestEpisode,
  TestEpisodeResult,
  VersionInfo,
  Workstream,
  WorkstreamPatch,
} from "./types";
import { api as mockApi } from "./mocks";

export class ApiError extends Error {
  status?: number;
  detail?: unknown;

  constructor(message: string, status?: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const data = detail as { error?: unknown; checks?: unknown };
    const parts = [];
    if (typeof data.error === "string") parts.push(data.error);
    if (Array.isArray(data.checks)) {
      const failed = data.checks
        .filter((check) => check && typeof check === "object" && (check as { ok?: unknown }).ok === false)
        .map((check) => String((check as { name?: unknown }).name ?? "check"));
      if (failed.length > 0) parts.push(`failed: ${failed.join(", ")}`);
    }
    if (parts.length > 0) return parts.join(" · ");
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }
  return fallback;
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(new URL(path, window.location.origin), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail: unknown = "";
    try {
      const body = (await res.json()) as { detail?: unknown };
      detail = body.detail ?? "";
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detailMessage(detail, `${res.status} on ${path}`), res.status, detail);
  }
  return res.json();
}

async function httpText(path: string, init?: RequestInit): Promise<string> {
  const res = await fetch(new URL(path, window.location.origin), init);
  if (!res.ok) throw new Error(`${res.status} on ${path}`);
  return res.text();
}

const realApi = {
  version: () => http<VersionInfo>("/api/version"),
  me: () => http<AuthInfo>("/api/auth/me"),
  logout: async () => {
    await http("/api/auth/logout", { method: "POST" });
  },
  overview: () => http<Overview>("/api/overview"),
  projects: () => http<Project[]>("/api/projects"),
  createProject: (body: ProjectCreate) =>
    http<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  startProject: (id: string, body: ProjectStart) =>
    http<Project>(`/api/projects/${id}/start`, { method: "POST", body: JSON.stringify(body) }),
  project: (id: string) => http<ProjectDetail>(`/api/projects/${id}`),
  createDirective: (id: string, text: string) =>
    http<Directive>(`/api/projects/${id}/directives`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  patchProject: (id: string, patch: ProjectPatch) =>
    http<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  updateWorkstream: (projectId: string, workstreamId: string, patch: WorkstreamPatch) =>
    http<Workstream>(`/api/projects/${projectId}/workstreams/${workstreamId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  createProjectRepo: (id: string, body: ProjectRepoCreate) =>
    http<{ project: Project; repo: GithubRepo }>(`/api/projects/${id}/repo`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  startIntake: (id: string, backend = "") =>
    http<AgentConversation>(`/api/projects/${id}/intake/start`, {
      method: "POST",
      body: JSON.stringify({ backend }),
    }),
  writeMission: (id: string, backend = "") =>
    http<{ conversation: AgentConversation; task: Task }>(`/api/projects/${id}/intake/write-mission`, {
      method: "POST",
      body: JSON.stringify({ backend }),
    }),
  finalizeIntake: (id: string) =>
    http<{ conversation: AgentConversation }>(`/api/projects/${id}/intake/finalize`, { method: "POST" }),
  conversationMessage: (id: string, body: IntakeMessage) =>
    http<{ conversation: AgentConversation; task?: Task }>(`/api/conversations/${id}/message`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  issuesPreflight: (id: string) =>
    http<PreflightResult>(`/api/projects/${id}/issues-preflight`, { method: "POST" }),
  scanIssues: (id: string) =>
    http<ScanResult>(`/api/projects/${id}/scan-issues`, { method: "POST" }),
  workstreamPreflight: (projectId: string, workstreamId: string) =>
    http<PreflightResult>(`/api/projects/${projectId}/workstreams/${workstreamId}/preflight`, { method: "POST" }),
  syncIssues: (projectId: string, workstreamId: string) =>
    http<ScanResult>(`/api/projects/${projectId}/workstreams/${workstreamId}/sync`, { method: "POST" }),
  runIssues: (
    projectId: string,
    workstreamId: string,
    body: { scope: "selected" | "all_open_now" | "scan_only"; issue_numbers?: number[] },
  ) =>
    http<IssueRunResult>(`/api/projects/${projectId}/workstreams/${workstreamId}/issue-runs`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelIssueRun: (id: string) => http<IssueRun>(`/api/issue-runs/${id}/cancel`, { method: "POST" }),
  checkCi: (projectId: string, workstreamId: string) =>
    http<CiCheckResult>(`/api/projects/${projectId}/workstreams/${workstreamId}/check-ci`, { method: "POST" }),
  refreshTests: (projectId: string, workstreamId: string) =>
    http<{ task: Task }>(`/api/projects/${projectId}/workstreams/${workstreamId}/test-refresh`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  runTests: (
    projectId: string,
    workstreamId: string,
    body: { scope: "priority" | "full" | "selected"; story_keys?: string[]; max_stories?: number },
  ) =>
    http<TestEpisodeResult>(`/api/projects/${projectId}/workstreams/${workstreamId}/test-episodes`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelTestEpisode: (id: string) => http<TestEpisode>(`/api/test-episodes/${id}/cancel`, { method: "POST" }),
  answerQuestion: (id: string, answer: string) =>
    http<Question>(`/api/questions/${id}/answer`, { method: "POST", body: JSON.stringify({ answer }) }),
  feedback: async (project_id: string, target_id: string, verdict: "up" | "down", comment: string) => {
    await http("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ project_id, target_id, verdict, comment }),
    });
  },
  task: (id: string) => http<Task>(`/api/tasks/${id}`),
  cancelTask: (id: string) => http<Task>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  trace: (id: string) => httpText(`/api/tasks/${id}/trace`),
  resources: () => http<ResourcesPayload>("/api/resources"),
  startLocalRunner: () =>
    http<NonNullable<ResourcesPayload["local_runner"]>>("/api/local-runner/start", { method: "POST" }),
  updateLocalRunner: (patch: { autostart: boolean }) =>
    http<NonNullable<ResourcesPayload["local_runner"]>>("/api/local-runner", { method: "PATCH", body: JSON.stringify(patch) }),
  probeResource: (id: string) =>
    http(`/api/resources/${id}/probe`, { method: "POST" }),
  updateResource: (id: string, patch: { enabled?: boolean; disabled_reason?: string }) =>
    http(`/api/resources/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  forgetMachine: async (id: string) => {
    await http(`/api/machines/${id}`, { method: "DELETE" });
  },
  subscriptions: () => http<Subscription[]>("/api/subscriptions"),
  addSubscription: (provider: string, plan: string, licensing_mode: LicensingMode, notes: string) =>
    http<Subscription>("/api/subscriptions", {
      method: "POST",
      body: JSON.stringify({ provider, plan, licensing_mode, notes }),
    }),
  deleteSubscription: async (id: string) => {
    await http(`/api/subscriptions/${id}`, { method: "DELETE" });
  },
  humanTodos: () => http<HumanTodo[]>("/api/human-todos"),
  completeHumanTodo: (id: string) =>
    http<HumanTodo>(`/api/human-todos/${id}/done`, { method: "POST" }),
  humanTasks: () => http<HumanTodo[]>("/api/human-tasks"),
  completeHumanTask: (id: string) =>
    http<HumanTodo>(`/api/human-tasks/${id}/done`, { method: "POST" }),
  orgContext: async () => (await http<{ text: string }>("/api/org-context")).text,
  setOrgContext: async (text: string) => {
    await http("/api/org-context", { method: "PUT", body: JSON.stringify({ text }) });
  },
  githubRepos: () => http<GithubRepo[]>("/api/github/repos"),
  validateGithubRepo: (ref: string) =>
    http<GithubRepo>(`/api/github/repos/validate?ref=${encodeURIComponent(ref)}`),
  storage: () => http<StorageInfo>("/api/storage"),
};

export const api: typeof realApi = import.meta.env.VITE_MOCK === "1" ? (mockApi as typeof realApi) : realApi;

function readCache<T>(key?: string): T | null {
  if (!key) return null;
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function writeCache(key: string | undefined, value: unknown) {
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota / private mode — caching is best-effort */
  }
}

/**
 * Poll `fn` every `intervalMs`; re-runs when `deps` change. `refresh()` forces an
 * immediate re-fetch (use after mutations).
 *
 * `enabled: false` holds polling (and leaves `data` null) until a prerequisite —
 * e.g. auth — is ready, so callers never see a premature empty payload.
 * `cacheKey` seeds the first render from localStorage and persists each success
 * (stale-while-revalidate), so a reload paints last state instantly.
 */
export function usePoll<T>(
  fn: () => Promise<T>,
  deps: unknown[],
  intervalMs = 4000,
  opts: { enabled?: boolean; cacheKey?: string } = {},
) {
  const { enabled = true, cacheKey } = opts;
  const [data, setData] = useState<T | null>(() => readCache<T>(cacheKey));
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const store = useCallback(
    (d: T) => {
      setData(d);
      setFailed(false);
      setError(null);
      writeCache(cacheKey, d);
    },
    [cacheKey],
  );

  const refresh = useCallback(async () => {
    try {
      store(await fnRef.current());
    } catch (err) {
      setFailed(true);
      setError(err);
    }
  }, [store]);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const tick = async () => {
      try {
        const d = await fnRef.current();
        if (alive) store(d);
      } catch (err) {
        if (alive) {
          setFailed(true);
          setError(err);
        }
      }
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, intervalMs]);

  return { data, failed, error, refresh };
}

// ---- formatting helpers ----------------------------------------------------

export function repoShort(url: string): string {
  const tail = url.split("/").pop() ?? url;
  return tail.replace(/\.git$/, "");
}

/** Normalize git URLs and owner/repo refs for deduping (owner/repo, lowercased). */
export function repoKey(url: string): string {
  const trimmed = url.trim().replace(/\.git$/i, "");
  const ownerRepo = trimmed.match(/^[\w.-]+\/[\w.-]+$/);
  if (ownerRepo) return trimmed.toLowerCase();
  const ssh = trimmed.match(/^git@github\.com:(.+)$/i);
  if (ssh) return ssh[1].toLowerCase();
  const https = trimmed.match(/^https?:\/\/github\.com\/(.+)$/i);
  if (https) return https[1].toLowerCase();
  return trimmed.toLowerCase();
}

export function ago(epoch: number): string {
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function duration(start: number, end: number): string {
  if (!start) return "—";
  const s = Math.max(0, (end || Date.now() / 1000) - start);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function money(usd: number): string {
  return usd >= 100 ? `$${usd.toFixed(0)}` : `$${usd.toFixed(2)}`;
}

export function countdown(until: number): string {
  const s = Math.max(0, until - Date.now() / 1000);
  if (s === 0) return "";
  if (s < 60) return `${Math.ceil(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
