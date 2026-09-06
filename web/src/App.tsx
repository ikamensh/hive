import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useOutletContext } from "react-router-dom";
import { IconChevronDown, IconChartBar, IconFolders, IconHome, IconInbox, IconMenu2, IconSettings, IconX } from "@tabler/icons-react";
import { ApiError, api, usePoll } from "./api";
import type { AuthInfo, Overview } from "./types";
import "./app.css";

type WorkspaceContext = ReturnType<typeof usePoll<Overview>> & { inboxCount: number | null };
export function useOverview() { return useOutletContext<WorkspaceContext>(); }

function AccountMenu({ auth, version }: { auth: AuthInfo; version: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const outside = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && open) { setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  const signOut = async () => {
    setBusy(true); setError("");
    try { await api.logout(); window.location.href = "/"; }
    catch (err) { setError(String(err)); setBusy(false); }
  };
  return <div className="account-menu" ref={root}>
    <button ref={trigger} type="button" className="account-trigger" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="account-details">
      <span className="account-avatar">{auth.user.github_login.slice(0, 2).toUpperCase()}</span>
      <span className="account-name">@{auth.user.github_login}<small>{auth.auth_mode === "dev" ? "Local workspace" : "Workspace account"}</small></span>
      <IconChevronDown size={16} aria-hidden />
    </button>
    {open && <div className="account-popover" id="account-details">
      <strong>@{auth.user.github_login}</strong>
      <p>{version && `Hive ${version}`}</p>
      <NavLink to="/users" onClick={() => setOpen(false)}>Workspace members</NavLink>
      <NavLink to="/settings" onClick={() => setOpen(false)}>Workspace settings</NavLink>
      {auth.auth_mode === "dev" ? <small>Local development signs you in automatically.</small> :
        <button type="button" className="ghost" onClick={signOut} disabled={busy}>{busy ? "Signing out…" : "Sign out"}</button>}
      {error && <p role="alert">{error}</p>}
    </div>}
  </div>;
}

export default function App() {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const version = usePoll(() => api.version(), [], 60000, { cacheKey: "hive-version" });
  const auth = usePoll(() => api.me(), [], 30000);
  const overviewCacheKey = auth.data ? `hive-overview:v2:${auth.data.workspace.id}:${auth.data.user.id}` : undefined;
  const poll = usePoll(() => api.overview(), [auth.data?.workspace.id], 4000, { enabled: !!auth.data, cacheKey: overviewCacheKey });
  const inbox = usePoll(() => api.inbox({ limit: 1 }), [auth.data?.workspace.id], 4000, { enabled: !!auth.data });
  const inboxCount = inbox.data?.counts.all ?? null;
  const versionLabel = version.data?.version ?? auth.data?.version?.version ?? "";

  useEffect(() => { setNavOpen(false); }, [location.pathname]);
  useEffect(() => () => {
    if (overviewCacheKey) localStorage.removeItem(overviewCacheKey);
  }, [overviewCacheKey]);
  useEffect(() => { document.title = versionLabel ? `Hive ${versionLabel}` : "Hive"; }, [versionLabel]);

  if (!auth.data && auth.failed) {
    const needsLogin = auth.error instanceof ApiError && auth.error.status === 401;
    return <div className="login-shell"><section className="login-panel">
      <div className="login-wordmark">Hive</div>
      <h1>{needsLogin ? "Sign in to your workspace" : "Chief unreachable"}</h1>
      {needsLogin ? <a className="login-button" href="/api/auth/github/start">Continue with GitHub</a> :
        <p className="muted">Checking the connection. Your workspace will appear when the chief is available.</p>}
    </section></div>;
  }

  return <div className="workspace-shell">
    <a className="skip-link" href="#workspace-content">Skip to content</a>
    <aside className={`workspace-sidebar ${navOpen ? "is-open" : ""}`}>
      <div className="sidebar-brand-row">
        <NavLink to="/" className="sidebar-brand" aria-label="Hive overview">Hive</NavLink>
        <button type="button" className="sidebar-toggle" aria-label={navOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={navOpen} aria-controls="workspace-navigation" onClick={() => setNavOpen(!navOpen)}>
          {navOpen ? <IconX size={24} aria-hidden /> : <IconMenu2 size={24} aria-hidden />}
        </button>
      </div>
      <nav className="workspace-navigation" id="workspace-navigation" aria-label="Workspace">
        <NavLink to="/" end><IconHome aria-hidden /><span>Overview</span></NavLink>
        <NavLink to="/projects"><IconFolders aria-hidden /><span>Projects</span></NavLink>
        <NavLink to="/needs-you"><IconInbox aria-hidden /><span>Inbox</span>{inboxCount !== null && inboxCount > 0 && <span className="nav-count">{inboxCount}</span>}</NavLink>
        <NavLink to="/machines"><IconChartBar aria-hidden /><span>Capacity</span></NavLink>
      </nav>
      <div className="sidebar-footer">
        <NavLink className="sidebar-settings" to="/settings"><IconSettings size={22} aria-hidden /><span>Settings</span></NavLink>
        {auth.data && <AccountMenu auth={auth.data} version={versionLabel} />}
      </div>
    </aside>
    <main id="workspace-content" className="workspace-content" tabIndex={-1}>
      {poll.failed && <div className="offline-banner" role="alert">Chief unreachable. Showing the last observation while reconnecting.</div>}
      <Outlet context={{ ...poll, inboxCount }} />
    </main>
  </div>;
}
