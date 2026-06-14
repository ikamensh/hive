import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AuthInfo,
  HumanTask,
  Project,
  ProjectCreate,
  ProjectDetail,
  ProjectPatch,
  ProjectStart,
  Question,
  ResourcesPayload,
  Subscription,
  Task,
} from "./types";
import { api as mockApi } from "./mocks";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  // Resolve against origin: a document URL with embedded user:pass@ credentials
  // would otherwise make relative-URL fetch() throw.
  const res = await fetch(new URL(path, window.location.origin), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const err = new Error(`${res.status} on ${path}`);
    (err as Error & { status?: number }).status = res.status;
    throw err;
  }
  return res.json();
}

async function httpText(path: string, init?: RequestInit): Promise<string> {
  const res = await fetch(new URL(path, window.location.origin), init);
  if (!res.ok) throw new Error(`${res.status} on ${path}`);
  return res.text();
}

const realApi = {
  me: () => http<AuthInfo>("/api/auth/me"),
  logout: async () => {
    await http("/api/auth/logout", { method: "POST" });
  },
  projects: () => http<Project[]>("/api/projects"),
  createProject: (body: ProjectCreate) =>
    http<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  startProject: (id: string, body: ProjectStart) =>
    http<Project>(`/api/projects/${id}/start`, { method: "POST", body: JSON.stringify(body) }),
  project: (id: string) => http<ProjectDetail>(`/api/projects/${id}`),
  patchProject: (id: string, patch: ProjectPatch) =>
    http<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  answerQuestion: (id: string, answer: string) =>
    http<Question>(`/api/questions/${id}/answer`, { method: "POST", body: JSON.stringify({ answer }) }),
  feedback: async (project_id: string, target_id: string, verdict: "up" | "down", comment: string) => {
    await http("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ project_id, target_id, verdict, comment }),
    });
  },
  task: (id: string) => http<Task>(`/api/tasks/${id}`),
  trace: (id: string) => httpText(`/api/tasks/${id}/trace`),
  resources: () => http<ResourcesPayload>("/api/resources"),
  startLocalRunner: () =>
    http<NonNullable<ResourcesPayload["local_runner"]>>("/api/local-runner/start", { method: "POST" }),
  probeResource: (id: string) =>
    http(`/api/resources/${id}/probe`, { method: "POST" }),
  subscriptions: () => http<Subscription[]>("/api/subscriptions"),
  addSubscription: (provider: string, plan: string, notes: string) =>
    http<Subscription>("/api/subscriptions", {
      method: "POST",
      body: JSON.stringify({ provider, plan, notes }),
    }),
  deleteSubscription: async (id: string) => {
    await http(`/api/subscriptions/${id}`, { method: "DELETE" });
  },
  humanTasks: () => http<HumanTask[]>("/api/human-tasks"),
  completeHumanTask: (id: string) =>
    http<HumanTask>(`/api/human-tasks/${id}/done`, { method: "POST" }),
  orgContext: async () => (await http<{ text: string }>("/api/org-context")).text,
  setOrgContext: async (text: string) => {
    await http("/api/org-context", { method: "PUT", body: JSON.stringify({ text }) });
  },
};

export const api: typeof realApi = import.meta.env.VITE_MOCK === "1" ? (mockApi as typeof realApi) : realApi;

/** Poll `fn` every `intervalMs`; re-runs when `deps` change. `refresh()` forces an immediate re-fetch (use after mutations). */
export function usePoll<T>(fn: () => Promise<T>, deps: unknown[], intervalMs = 4000) {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const refresh = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await fnRef.current();
        if (alive) {
          setData(d);
          setFailed(false);
        }
      } catch {
        if (alive) setFailed(true);
      }
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, failed, refresh };
}

// ---- formatting helpers ----------------------------------------------------

export function repoShort(url: string): string {
  const tail = url.split("/").pop() ?? url;
  return tail.replace(/\.git$/, "");
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
