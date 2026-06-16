import { useState, type FormEvent } from "react";
import { repoShort } from "../../api";
import { RepoListEditor } from "../../components/RepoPicker";
import {
  AUTONOMY_OPTIONS,
  GuessSlider,
  Markdown,
  MODE_OPTIONS,
  SegPicker,
} from "../../components/shared";
import type { Project, ProjectPatch, WorkItem, Workstream } from "../../types";

export function ProjectSettings({
  project,
  workstreams,
  onPatch,
  onPatchWorkstream,
}: {
  project: Project;
  onPatch: (p: ProjectPatch) => void;
  workstreams: Workstream[];
  onPatchWorkstream: (workstreamId: string, patch: { enabled?: boolean }) => Promise<void>;
}) {
  const [memberRepos, setMemberRepos] = useState(project.member_repos);
  const [dailyBudget, setDailyBudget] = useState(
    project.daily_budget_usd > 0 ? String(project.daily_budget_usd) : "",
  );
  const [busy, setBusy] = useState(false);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const budget = parseFloat(dailyBudget);
    await onPatch({
      member_repos: memberRepos.map((s) => s.trim()).filter(Boolean),
      daily_budget_usd: Number.isFinite(budget) && budget >= 0 ? budget : 0,
    });
    setBusy(false);
  };

  return (
    <details className="project-settings">
      <summary>settings</summary>
      <form onSubmit={save} className="settings-form">
        <label>
          spec repo
          <input value={project.spec_repo} readOnly />
        </label>
        <label>
          member repos
          <RepoListEditor repos={memberRepos} onChange={setMemberRepos} />
        </label>
        <label>
          daily budget (USD)
          <input
            type="number"
            min={0}
            step={1}
            value={dailyBudget}
            onChange={(e) => setDailyBudget(e.target.value)}
            placeholder="0"
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "saving..." : "save settings"}
        </button>
      </form>
      <div className="workstream-settings">
        <h3>workstreams</h3>
        {workstreams.map((workstream) => (
          <div className="workstream-setting" key={workstream.id}>
            <div>
              <strong>{workstream.title}</strong>
              <span className="muted">
                {workstream.kind.replace(/_/g, " ")}
                {workstream.repo ? ` - ${repoShort(workstream.repo)}` : ""}
              </span>
            </div>
            <button
              type="button"
              className={`switch ${workstream.enabled ? "on" : ""}`}
              onClick={() => onPatchWorkstream(workstream.id, { enabled: !workstream.enabled })}
              disabled={workstream.kind === "iteration"}
              title={workstream.kind === "iteration" ? "iteration work is controlled by project pause" : undefined}
              aria-pressed={workstream.enabled}
            >
              <i />
            </button>
          </div>
        ))}
      </div>
    </details>
  );
}

export function TogglesBar({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
  return (
    <section className="toggles">
      <div className="toggle-cell">
        <span className="toggle-label">mode</span>
        <SegPicker value={project.mode} options={MODE_OPTIONS} onChange={(mode) => onPatch({ mode })} />
      </div>
      <div className="toggle-cell">
        <span className="toggle-label">autonomy</span>
        <SegPicker
          value={project.autonomy}
          options={AUTONOMY_OPTIONS}
          onChange={(autonomy) => onPatch({ autonomy })}
        />
      </div>
      <div className="toggle-cell grow">
        <span className="toggle-label">guess propensity</span>
        <GuessSlider value={project.guess_propensity} onChange={(guess_propensity) => onPatch({ guess_propensity })} />
      </div>
      <div className="toggle-cell">
        <span className="toggle-label">prod deploys</span>
        <button
          className={`switch ${project.prod_deploys ? "on" : ""}`}
          onClick={() => onPatch({ prod_deploys: !project.prod_deploys })}
          aria-pressed={project.prod_deploys}
        >
          <i />
        </button>
      </div>
      <div className="toggle-cell">
        <span className="toggle-label">paused</span>
        <button
          className={`switch warn ${project.paused ? "on" : ""}`}
          onClick={() => onPatch({ paused: !project.paused })}
          aria-pressed={project.paused}
        >
          <i />
        </button>
      </div>
    </section>
  );
}

export function GoalBanner({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
  const [note, setNote] = useState("");
  return (
    <section className="goal-banner reveal">
      <div className="goal-text">
        <h2>Goal complete</h2>
        {project.goal_complete_note && <Markdown text={project.goal_complete_note} />}
      </div>
      <form
        className="goal-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (note.trim()) onPatch({ new_iteration_note: note.trim() });
        }}
      >
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder="What should the next iteration pursue?"
        />
        <button type="submit" disabled={!note.trim()}>
          start next iteration
        </button>
      </form>
    </section>
  );
}

export function WorkstreamCard({ ws }: { ws: WorkItem }) {
  return (
    <article className={`ws-card ws-${ws.status}`}>
      <header>
        <h3>{ws.title}</h3>
        <span className={`chip chip-${ws.status}`}>{ws.status}</span>
      </header>
      {ws.description && <p>{ws.description}</p>}
      {ws.status === "parked" && ws.parked_reason && <p className="parked-reason">{ws.parked_reason}</p>}
    </article>
  );
}
