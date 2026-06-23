import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useOutletContext } from "react-router-dom";
import { ApiError, api, usePoll } from "./api";
import { useTheme } from "./theme";
import type { AuthInfo, Overview, StorageInfo } from "./types";

export function useOverview() {
  return useOutletContext<ReturnType<typeof usePoll<Overview>>>();
}

function storageLabel(storage: StorageInfo): string {
  if (storage.fully_managed) return "persistence: managed";
  if (storage.backend === "firestore") return "persistence: mixed storage";
  if (storage.backend === "file") return "persistence: legacy local";
  return "persistence: test memory";
}

function VersionChip({ version }: { version: string }) {
  if (!version) return null;
  return (
    <span className="version-chip" title={`Hive chief version ${version}`}>
      v{version}
    </span>
  );
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
  const version = usePoll(() => api.version(), [], 60000, { cacheKey: "hive-version" });
  const auth = usePoll(() => api.me(), [], 30000);
  const poll = usePoll(() => api.overview(), [auth.data?.workspace.id], 4000, {
    enabled: !!auth.data,
    cacheKey: "hive-overview",
  });
  const { theme, toggle } = useTheme();
  const attention = poll.data?.totals.needs_you ?? 0;
  const authNeedsLogin = auth.error instanceof ApiError && auth.error.status === 401;
  const versionLabel = version.data?.version ?? auth.data?.version?.version ?? "";

  useEffect(() => {
    document.title = versionLabel ? `hive ${versionLabel} — chief` : "hive — chief";
  }, [versionLabel]);

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
    if (!authNeedsLogin) {
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
              <VersionChip version={versionLabel} />
            </div>
            <h1>Chief unreachable</h1>
            <p className="modal-hint">Retrying...</p>
          </section>
        </div>
      );
    }

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
            <VersionChip version={versionLabel} />
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
            <small>chief</small>
          </NavLink>
          <VersionChip version={versionLabel} />
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
            home
          </NavLink>
          <NavLink to="/needs-you">needs you</NavLink>
          <NavLink to="/machines">machines</NavLink>
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
                d="M20.35 15.35A9 9 0 0 1 8.65 3.65 9 9 0 1 0 20.35 15.35z"
                fill="currentColor"
              />
            </svg>
          )}
        </button>
        <NavLink
          to="/needs-you"
          className={`q-counter ${attention > 0 ? "hot" : ""}`}
          title="open questions + human todos"
        >
          <span className="q-num">{poll.data ? attention : "–"}</span>
          <span className="q-label">need attention</span>
        </NavLink>
      </header>

      {poll.failed && (
        <div className="offline-banner" role="alert">
          <i className="dot" /> chief unreachable — retrying every 4s
        </div>
      )}

      <main>
        <Outlet context={poll} />
      </main>
    </div>
  );
}
