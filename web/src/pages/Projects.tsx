import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ago, countdown, repoShort } from "../api";
import { useOverview } from "../App";
import {
  AUTONOMY_OPTIONS,
  GuessSlider,
  MODE_OPTIONS,
  SegPicker,
  StateBadge,
} from "../components/shared";
import type { Autonomy, GuessPropensity, Mode, ProjectDetail } from "../types";

function ProjectRow({ detail, cooldownHint }: { detail: ProjectDetail; cooldownHint?: string }) {
  const { project, workstreams, tasks, questions } = detail;
  const open = questions.filter((q) => q.status === "open").length;
  const active = workstreams.filter((w) => w.status === "active").length;
  const running = tasks.filter((t) => t.status === "running").length;
  const cost = tasks.reduce((s, t) => s + t.cost_usd, 0);

  return (
    <Link to={`/p/${project.id}`} className="project-row">
      <div className="pr-name">
        <h2>{project.name}</h2>
        <span className="pr-repo">{repoShort(project.spec_repo)}</span>
        {project.paused && <span className="chip chip-paused">paused</span>}
      </div>
      <StateBadge state={project.state} questionCount={open} cooldownHint={cooldownHint} />
      <div className="pr-stats">
        <span>
          <b>{active}</b> active ws
        </span>
        <span>
          <b>{running}</b> running
        </span>
        <span>
          <b>{open}</b> questions
        </span>
        <span>
          <b>${cost.toFixed(0)}</b> spent
        </span>
        <span className="pr-age">{ago(project.created_at)}</span>
      </div>
    </Link>
  );
}

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [specRepo, setSpecRepo] = useState("");
  const [memberRepos, setMemberRepos] = useState("");
  const [mission, setMission] = useState("");
  const [iterationGoal, setIterationGoal] = useState("");
  const [mode, setMode] = useState<Mode>("build");
  const [autonomy, setAutonomy] = useState<Autonomy>("direct_push");
  const [guess, setGuess] = useState<GuessPropensity>("sometimes");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const p = await api.createProject({
        name: name.trim(),
        spec_repo: specRepo.trim(),
        member_repos: memberRepos
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        mission: mission.trim(),
        iteration_goal: iterationGoal.trim(),
        mode,
        autonomy,
        guess_propensity: guess,
      });
      navigate(`/p/${p.id}`);
    } catch {
      setError("create failed — is the control plane up?");
      setBusy(false);
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>New project</h2>
        <label>
          name
          <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus placeholder="atlas" />
        </label>
        <label>
          spec repo URL
          <input
            value={specRepo}
            onChange={(e) => setSpecRepo(e.target.value)}
            required
            placeholder="git@github.com:org/project-spec.git"
          />
        </label>
        <label>
          member repos (one per line)
          <textarea value={memberRepos} onChange={(e) => setMemberRepos(e.target.value)} rows={3} />
        </label>
        <label>
          mission
          <textarea
            value={mission}
            onChange={(e) => setMission(e.target.value)}
            rows={3}
            required
            placeholder="What should Hive understand about this project?"
          />
        </label>
        <label>
          first iteration goal
          <textarea
            value={iterationGoal}
            onChange={(e) => setIterationGoal(e.target.value)}
            rows={3}
            required
            placeholder="What should agents accomplish first?"
          />
        </label>
        <div className="dial-grid">
          <label>
            mode
            <SegPicker value={mode} options={MODE_OPTIONS} onChange={setMode} />
          </label>
          <label>
            autonomy
            <SegPicker value={autonomy} options={AUTONOMY_OPTIONS} onChange={setAutonomy} />
          </label>
        </div>
        <label>
          guess propensity
          <GuessSlider value={guess} onChange={setGuess} />
        </label>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            cancel
          </button>
          <button type="submit" disabled={busy}>
            {busy ? "creating…" : "create project"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function Projects() {
  const { data, failed } = useOverview();
  const [showNew, setShowNew] = useState(false);

  // Soonest backend cooldown to expire, shown on blocked_resources badges.
  const now = Date.now() / 1000;
  const cooldowns = (data?.resources.resources ?? [])
    .filter((r) => !r.available && r.cooldown_until > now)
    .map((r) => r.cooldown_until);
  const cooldownHint = cooldowns.length ? countdown(Math.min(...cooldowns)) : undefined;

  return (
    <div className="page page-projects">
      <div className="page-head">
        <h1>Projects</h1>
        <button onClick={() => setShowNew(true)}>+ new project</button>
      </div>

      {!data && !failed && <p className="muted">loading…</p>}
      {data && data.details.length === 0 && (
        <div className="empty-state">
          <p>No projects yet. The hive is quiet.</p>
        </div>
      )}
      <div className="project-list">
        {data?.details.map((d, i) => (
          <div key={d.project.id} className="reveal" style={{ animationDelay: `${i * 60}ms` }}>
            <ProjectRow
              detail={d}
              cooldownHint={d.project.state === "blocked_resources" ? cooldownHint : undefined}
            />
          </div>
        ))}
      </div>

      {showNew && <NewProjectModal onClose={() => setShowNew(false)} />}
    </div>
  );
}
