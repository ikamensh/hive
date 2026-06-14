import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useOutletContext } from "react-router-dom";
import { api, usePoll } from "./api";
import { useTheme } from "./theme";
import type { AuthInfo, ProjectDetail, ResourcesPayload, StorageInfo } from "./types";

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

function storageLabel(storage: StorageInfo): string {
  if (storage.backend === "firestore") return "persistence: cloud store";
  if (storage.backend === "file") return "persistence: local files";
  return "persistence: in-memory";
}

function AccountMenu({
  auth,
  loggingOut,
  onSignOut,
}: {
  auth: AuthInfo;
  loggingOut: boolean;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const isDev = auth.auth_mode === "dev";

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        type="button"
        className="account-chip"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        title={`Signed in as ${auth.user.github_login}`}
      >
        <span className="account-user">@{auth.user.github_login}</span>
        {isDev && <span className="account-mode">dev</span>}
        <svg className="account-chevron" viewBox="0 0 24 24" width="14" height="14" aria-hidden>
          <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div className="account-dropdown" role="menu">
          {isDev && (
            <p className="account-dropdown-hint">Dev auth auto-signs in — sign out needs GitHub auth mode.</p>
          )}
          <button
            type="button"
            role="menuitem"
            className="account-dropdown-item"
            disabled={loggingOut || isDev}
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [loggingOut, setLoggingOut] = useState(false);
  const auth = usePoll(() => api.me(), [], 30000);
  const poll = usePoll(
    () => (auth.data ? fetchOverview() : Promise.resolve(emptyOverview)),
    [auth.data?.workspace.id],
  );
  const { theme, toggle } = useTheme();
  const open = poll.data?.openQuestions ?? 0;
  const todos = poll.data?.openTodos ?? 0;
  const attention = open + todos;

  const signOut = async () => {
    setLoggingOut(true);
    try {
      await api.logout();
      window.location.href = "/";
    } catch {
      setLoggingOut(false);
    }
  };

  if (!auth.data && auth.failed) {
    return (
      <div className="login-shell">
        <section className="login-panel">
          <div className="brand login-brand">
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden>
              <path
                d="M12 1.5 21 6.75v10.5L12 22.5 3 17.25V6.75z"
                fill="none"
                stroke="var(--accent)"
                strokeWidth="1.8"
              />
              <path d="M12 6.5 16.5 9.25v5.5L12 17.5 7.5 14.75v-5.5z" fill="var(--accent-2)" opacity="0.7" />
            </svg>
            <span className="brand-word">hive</span>
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
        <div className="brand-cluster">
          <NavLink to="/" className="brand">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden>
              <path
                d="M12 1.5 21 6.75v10.5L12 22.5 3 17.25V6.75z"
                fill="none"
                stroke="var(--accent)"
                strokeWidth="1.8"
              />
              <path d="M12 6.5 16.5 9.25v5.5L12 17.5 7.5 14.75v-5.5z" fill="var(--accent-2)" opacity="0.7" />
            </svg>
            <span className="brand-word">hive</span>
            <small>control plane</small>
          </NavLink>
          {auth.data?.storage && (
            <NavLink
              to="/settings"
              className="storage-chip"
              title={auth.data.storage.store_path ?? auth.data.storage.gcp_project ?? undefined}
            >
              {storageLabel(auth.data.storage)}
            </NavLink>
          )}
        </div>
        <nav>
          <NavLink to="/" end>
            projects
          </NavLink>
          <NavLink to="/resources">resources</NavLink>
        </nav>
        {auth.data && <AccountMenu auth={auth.data} loggingOut={loggingOut} onSignOut={signOut} />}
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
