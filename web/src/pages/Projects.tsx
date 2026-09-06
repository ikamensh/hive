import { useState } from "react";
import { Link } from "react-router-dom";
import { useOverview } from "../App";
import { repoShort } from "../api";
import { StateBadge } from "../components/shared";
import { NewWorkButton } from "../features/work/NewWork";
import type { OverviewProject } from "../types";
import "../features/project/workspace.css";

function planAction(plan: OverviewProject["plan"]) {
  if (!plan) return "Start work";
  if (plan.status === "draft") return "Review plan";
  if (plan.status === "approved") return "View progress";
  return "Inspect results";
}

function PlanProgress({ plan }: { plan: OverviewProject["plan"] }) {
  if (!plan) return <span className="muted">No plan yet</span>;
  return <div className="portfolio-progress">
    <span>{plan.counts.done} landed</span>
    <span>{plan.counts.cancelled} cancelled</span>
    <span>{plan.counts.remaining} remaining</span>
    {plan.outcome === "partial" && <small>Execution ended with cancellations</small>}
    {plan.outcome === "abandoned" && <small>Plan stopped</small>}
    {plan.outcome === "done" && <small>Plan delivered</small>}
  </div>;
}

export default function ProjectsPage() {
  const { data, failed, refresh } = useOverview();
  const [query, setQuery] = useState("");
  const rows = (data?.projects ?? []).filter((project) =>
    `${project.name} ${project.plan?.goal ?? ""} ${project.spec_repo}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  return <div className="page project-workspace">
    <header className="workspace-header">
      <div className="workspace-heading">
        <h1>Projects</h1>
        <p>Your projects, their current work, and the next step.</p>
      </div>
      <NewWorkButton onStarted={refresh} />
    </header>
    <div className="workspace-filter">
      <input type="search" aria-label="Search projects"
        placeholder="Search projects, goals, or repositories…"
        value={query} onChange={(event) => setQuery(event.target.value)} />
      <span className="muted">{rows.length} {rows.length === 1 ? "project" : "projects"}</span>
    </div>
    {failed && <p className="form-error" role="alert">
      Could not refresh projects. {data ? "Shown information may be out of date." : "Retrying…"}
    </p>}
    {!data ? (!failed && <p className="muted">Loading projects…</p>) : rows.length === 0 ?
      <div className="workspace-empty">
        <h2>{query ? "No matching projects" : "Start your first project"}</h2>
        <p>{query ? "Try another name, goal, or repository." : "Bring a prepared plan or describe what you want to achieve."}</p>
      </div> :
      <div className="workspace-table-wrap portfolio-table-wrap">
        <table className="workspace-table portfolio-table">
          <thead><tr><th>Project / goal</th><th>Status</th><th>Progress</th><th>Next step</th></tr></thead>
          <tbody>{rows.map((project) => {
            const planUrl = `/p/${encodeURIComponent(project.id)}${project.plan ? `?plan=${encodeURIComponent(project.plan.id)}` : ""}`;
            return <tr key={project.id}>
              <td className="portfolio-project">
                <Link to={`/p/${encodeURIComponent(project.id)}`}>{project.name}</Link>
                {project.plan && <p className="portfolio-goal" title={project.plan.goal}>{project.plan.goal}</p>}
                <small title={project.spec_repo}>{repoShort(project.spec_repo) || "Repository not connected"}</small>
              </td>
              <td className="portfolio-status">
                {project.paused ? <span className="chip">Paused</span> : <StateBadge state={project.state} attentionCount={project.counts.questions + project.counts.blockers} />}
                {project.state_reason && <p title={project.state_reason}>{project.state_reason}</p>}
              </td>
              <td><PlanProgress plan={project.plan} /></td>
              <td className="portfolio-next"><Link to={planUrl}>{planAction(project.plan)}</Link></td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
  </div>;
}
