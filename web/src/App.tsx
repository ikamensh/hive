import { NavLink, Outlet, useOutletContext } from "react-router-dom";
import { api, usePoll } from "./api";
import { useTheme } from "./theme";
import type { ProjectDetail, ResourcesPayload } from "./types";

export interface Overview {
  details: ProjectDetail[];
  openQuestions: number;
  openTodos: number;
  resources: ResourcesPayload;
}

async function fetchOverview(): Promise<Overview> {
  const [projects, resources, humanTasks] = await Promise.all([
    api.projects(),
    api.resources(),
    api.humanTasks(),
  ]);
  const details = await Promise.all(projects.map((p) => api.project(p.id)));
  const openQuestions = details.reduce(
    (n, d) => n + d.questions.filter((q) => q.status === "open").length,
    0,
  );
  const openTodos = humanTasks.filter((t) => t.status === "open").length;
  return { details, openQuestions, openTodos, resources };
}

export function useOverview() {
  return useOutletContext<ReturnType<typeof usePoll<Overview>>>();
}

const emptyOverview: Overview = {
  details: [],
  openQuestions: 0,
  openTodos: 0,
  resources: { machines: [], runners: [], resources: [] },
};

export default function App() {
  const auth = usePoll(() => api.me(), [], 30000);
  const poll = usePoll(
    () => (auth.data ? fetchOverview() : Promise.resolve(emptyOverview)),
    [auth.data?.workspace.id],
  );
  const { theme, toggle } = useTheme();
  const open = poll.data?.openQuestions ?? 0;
  const todos = poll.data?.openTodos ?? 0;
  const attention = open + todos;

  if (!auth.data && auth.failed) {
    return (
      <div className="login-shell">
        <section className="login-panel">
          <div className="brand login-brand">
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden>
              <path
                d="M12 1.5 21 6.75v10.5L12 22.5 3 17.25V6.75z"
                fill="none"
                stroke="var(--honey)"
                strokeWidth="1.8"
              />
              <path d="M12 6.5 16.5 9.25v5.5L12 17.5 7.5 14.75v-5.5z" fill="var(--honey)" opacity="0.55" />
            </svg>
            <span>hive</span>
          </div>
          <h1>Sign in</h1>
          <a className="login-button" href="/api/auth/github/start">
            Continue with GitHub
          </a>
        </section>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden>
            <path
              d="M12 1.5 21 6.75v10.5L12 22.5 3 17.25V6.75z"
              fill="none"
              stroke="var(--honey)"
              strokeWidth="1.8"
            />
            <path d="M12 6.5 16.5 9.25v5.5L12 17.5 7.5 14.75v-5.5z" fill="var(--honey)" opacity="0.55" />
          </svg>
          <span>hive</span>
          <small>control plane</small>
        </NavLink>
        <nav>
          <NavLink to="/" end>
            projects
          </NavLink>
          <NavLink to="/resources">resources</NavLink>
        </nav>
        <button
          type="button"
          className="theme-toggle ghost"
          onClick={toggle}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? (
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
              <circle cx="12" cy="12" r="4" fill="currentColor" />
              <path
                d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
              <path
                d="M21 14.5A7.5 7.5 0 0 1 9.5 3 6.5 6.5 0 1 0 14.5 21 7.5 7.5 0 0 1 21 14.5z"
                fill="currentColor"
              />
            </svg>
          )}
        </button>
        <div
          className={`q-counter ${attention > 0 ? "hot" : ""}`}
          title={`${open} open questions, ${todos} human todos`}
        >
          <span className="q-num">{poll.data ? attention : "–"}</span>
          <span className="q-label">need attention</span>
        </div>
      </header>

      {poll.failed && (
        <div className="offline-banner" role="alert">
          <i className="dot" /> control plane unreachable — retrying every 4s
        </div>
      )}

      <main>
        <Outlet context={poll} />
      </main>
    </div>
  );
}
