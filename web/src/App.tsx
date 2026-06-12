import { NavLink, Outlet, useOutletContext } from "react-router-dom";
import { api, usePoll } from "./api";
import type { ProjectDetail, ResourcesPayload } from "./types";

export interface Overview {
  details: ProjectDetail[];
  openQuestions: number;
  resources: ResourcesPayload;
}

async function fetchOverview(): Promise<Overview> {
  const [projects, resources] = await Promise.all([api.projects(), api.resources()]);
  const details = await Promise.all(projects.map((p) => api.project(p.id)));
  const openQuestions = details.reduce(
    (n, d) => n + d.questions.filter((q) => q.status === "open").length,
    0,
  );
  return { details, openQuestions, resources };
}

export function useOverview() {
  return useOutletContext<ReturnType<typeof usePoll<Overview>>>();
}

export default function App() {
  const poll = usePoll(fetchOverview, []);
  const open = poll.data?.openQuestions ?? 0;

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
        <div className={`q-counter ${open > 0 ? "hot" : ""}`} title="open questions across all projects">
          <span className="q-num">{poll.data ? open : "–"}</span>
          <span className="q-label">open questions</span>
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
