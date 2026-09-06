import { useState } from "react";
import { Link } from "react-router-dom";
import { api, usePoll } from "../../api";
import type { Plan, Project } from "../../types";
import "./planReadiness.css";

export function PlanReadiness({ plan, project, onChanged, onReviewItems }: {
  plan: Plan; project: Project; onChanged: () => void; onReviewItems: () => void;
}) {
  const { data, error, failed, refresh } = usePoll(() => api.planReadiness(plan.id),
    [plan.id, plan.revision, project.paused, project.validation_command]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const resume = async (fleet: boolean) => {
    setBusy(true);
    setActionError("");
    try {
      if (fleet) await api.setFleetPaused(false);
      else await api.patchProject(project.id, { paused: false });
      onChanged();
      await refresh();
    } catch (error) { setActionError(String(error)); }
    finally { setBusy(false); }
  };
  const blocked = data?.checks.filter(c => c.status === "blocked" || c.status === "waiting") ?? [];
  return (
    <section className="plan-readiness" aria-label="Execution readiness">
      <header>
        <h4>Execution readiness</h4>
        <span className={`chip ${!failed && blocked.length ? "readiness-warning" : ""}`}>
          {failed ? "Checks unavailable" : !data ? "Checking…" : blocked.length
            ? `${blocked.length} ${blocked.length === 1 ? "check needs" : "checks need"} attention`
            : data.ready ? "Capacity available" : "Capacity not checked"}
        </span>
        <button type="button" className="ghost" onClick={refresh}>Refresh checks</button>
      </header>
      {failed && <p role="alert" className="form-error">Could not refresh readiness: {String(error)}. Refresh to check current capacity.</p>}
      {actionError && <p role="alert" className="form-error">{actionError}</p>}
      {!failed && data && <>
        <ul className="readiness-checks">
          {data.checks.filter(c => c.status !== "info").map(check => (
            <li key={check.key} className={`readiness-${check.status}`}>
              <span aria-hidden>{check.status === "ready" ? "✓" : "!"}</span>
              <div><strong>{check.label}</strong><p>{check.detail}</p></div>
              {check.action === "resources" && <Link to="/resources">Manage agents</Link>}
              {check.action === "settings" && <Link to={`/p/${project.id}/settings`}>Project settings</Link>}
              {check.action === "items" && <button type="button" className="ghost" onClick={onReviewItems}>Review items</button>}
              {(check.action === "resume_project" || check.action === "resume_fleet") &&
                <button type="button" className="ghost" disabled={busy}
                  onClick={() => resume(check.action === "resume_fleet")}>
                  {check.action === "resume_fleet" ? "Resume Hive" : "Resume project"}
                </button>}
            </li>
          ))}
        </ul>
        <details className="readiness-details">
          <summary>What these checks cover</summary>
          {data.checks.filter(c => c.status === "info").map(check => (
            <p key={check.key}><strong>{check.label}</strong><br />{check.detail}</p>
          ))}
          <p>Current settings and observed capacity for the next build and review. An automatic reviewer follows the builder’s final agent choice. Capacity and allowances can change between sessions.</p>
          <p>Repository access and validation are proven during execution. Passing these checks does not reserve a runner or guarantee the plan will finish.</p>
        </details>
      </>}
      <p className="muted readiness-approval-note">Approval queues the work. If capacity is unavailable or Hive is paused, it waits.</p>
    </section>
  );
}
