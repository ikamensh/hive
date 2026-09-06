import { useState } from "react";
import { Link } from "react-router-dom";
import { api, repoShort, usePoll } from "../api";
import { StateBadge } from "../components/shared";
import { NewWorkButton } from "../features/work/NewWork";
import "../features/project/workspace.css";

export default function ProjectsPage() {
  const projects = usePoll(() => api.projects(), [], 5000);
  const [query, setQuery] = useState("");
  const rows = (projects.data ?? []).filter((project) =>
    `${project.name} ${project.spec_repo}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <div className="page project-workspace">
      <header className="workspace-header">
        <div className="workspace-heading">
          <h1>Projects</h1>
          <p>Your projects, their current work, and the next step.</p>
        </div>
        <NewWorkButton onStarted={projects.refresh} />
      </header>
      <div className="workspace-filter">
        <input
          type="search"
          aria-label="Search projects"
          placeholder="Search projects or repositories…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span className="muted">{rows.length} projects</span>
      </div>
      {projects.failed && (
        <p className="form-error" role="alert">
          Could not refresh projects.{" "}
          {projects.data
            ? "Shown information may be out of date."
            : "Please try again."}
        </p>
      )}
      {!projects.data && !projects.failed ? (
        <p className="muted">Loading projects…</p>
      ) : rows.length === 0 ? (
        <div className="workspace-empty">
          <h2>{query ? "No matching projects" : "Start your first project"}</h2>
          <p>
            {query
              ? "Try another name or repository."
              : "Bring a prepared plan or describe what you want to achieve."}
          </p>
        </div>
      ) : (
        <div className="workspace-table-wrap">
          <table className="workspace-table">
            <thead>
              <tr>
                <th>Project</th>
                <th>Status</th>
                <th>Repository</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((project) => (
                <tr key={project.id}>
                  <td>
                    <Link to={`/p/${project.id}`}>{project.name}</Link>
                  </td>
                  <td>
                    {project.paused ? (
                      <span className="chip">Paused</span>
                    ) : (
                      <StateBadge state={project.state} />
                    )}
                  </td>
                  <td>{repoShort(project.spec_repo) || "Not connected"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
