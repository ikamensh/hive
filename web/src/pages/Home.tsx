import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, countdown, duration, money, repoShort } from "../api";
import { useOverview } from "../App";
import { StateBadge } from "../components/shared";
import { ProjectActions } from "../features/project/controls";
import "./home.css";
import type {
  AgentStatus,
  OverviewAgent,
  OverviewMachine,
  OverviewProject,
  Overview,
  ProjectPatch,
} from "../types";

const AGENT_TONE: Record<AgentStatus, "ok" | "warn" | "bad" | "muted"> = {
  ready: "ok",
  cooldown: "warn",
  probing: "warn",
  probe: "warn",
  failed: "bad",
  offline: "muted",
  disabled: "muted",
};

const PLAN_OUTCOMES: Record<NonNullable<OverviewProject["plan"]>["outcome"], string> = {
  pending: "Awaiting approval", running: "Execution in progress", blocked: "Needs attention",
  stopping: "Stopping · waiting for attempts to finish", done: "All items landed",
  partial: "Execution ended with cancellations", abandoned: "Plan abandoned",
};

function agentLabel(agent: OverviewAgent): string {
  if (agent.status === "cooldown") {
    const left = countdown(agent.cooldown_until);
    return left ? `cooldown ${left}` : "cooldown";
  }
  if (agent.status === "probe") return "probe needed";
  return agent.status;
}

/** Soonest agent cooldown to expire — shown on blocked_resources project badges. */
function soonestCooldown(data: Overview): string | undefined {
  const now = Date.now() / 1000;
  const times = data.capacity.machines
    .flatMap((m) => m.agents)
    .filter((a) => a.status === "cooldown" && a.cooldown_until > now)
    .map((a) => a.cooldown_until);
  return times.length ? countdown(Math.min(...times)) : undefined;
}

function Kpi({
  icon,
  label,
  value,
  sub,
  hot,
  to,
}: {
  icon: string;
  label: string;
  value: React.ReactNode;
  sub: string;
  hot?: boolean;
  to?: string;
}) {
  const cls = `kpi ${hot ? "hot" : ""} ${to ? "link" : ""}`;
  const inner = (
    <>
      <span className="kpi-label">
        <i className={`ti ti-${icon}`} aria-hidden /> {label}
      </span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-sub">{sub}</span>
    </>
  );
  return to ? (
    <Link to={to} className={cls}>
      {inner}
    </Link>
  ) : (
    <div className={cls}>{inner}</div>
  );
}

function ProjectRow({
  p,
  cooldownHint,
  onPatch,
}: {
  p: OverviewProject;
  cooldownHint?: string;
  onPatch: (p: ProjectPatch) => Promise<void>;
}) {
  const { counts, plan } = p;
  const isIdle = p.state === "idle" || p.state === "idle_goal_complete";
  const actionLabel = !plan ? "Submit a plan" : plan.outcome === "pending"
    ? "Review plan" : ["done", "partial", "abandoned"].includes(plan.outcome)
      ? "Inspect results" : "View progress";
  const bits = [
    counts.running ? `${counts.running} running` : null,
    counts.blockers ? `${counts.blockers} blocked` : null,
    counts.questions ? `${counts.questions} questions` : null,
  ].filter(Boolean);
  return (
    <article className="home-project home-plan-project">
      <Link to={`/p/${p.id}${plan ? `?plan=${encodeURIComponent(plan.id)}` : ""}`} className="hp-content">
        <div className="hp-main">
          <div className="hp-title">
            <span className="hp-name">{p.name}</span>
            {p.spec_repo ? (
              <span className="hp-repo">{repoShort(p.spec_repo)}</span>
            ) : (
              <span className="chip chip-setup">setup needed</span>
            )}
            {p.paused && <span className="chip chip-paused">paused</span>}
          </div>
          {plan ? <>
            <h3 className="hp-goal">{plan.goal}</h3>
            <p className={`hp-outcome hp-outcome-${plan.outcome}`}>{PLAN_OUTCOMES[plan.outcome]}</p>
            <div className="hp-progress" aria-label="Plan progress">
              <strong>{plan.counts.done} landed</strong>
              <span>{plan.counts.cancelled} cancelled</span>
              <span>{plan.counts.remaining} remaining</span>
            </div>
          </> : <p className="hp-no-plan">No plan yet. {p.spec_repo
            ? "Submit a plan to start an iteration."
            : "Connect a repository to submit your first plan."}</p>}
          {bits.length > 0 && <div className="hp-stats">{bits.join(" · ")}</div>}
          {p.state_reason && !isIdle && (
            <div className="hp-reason">{p.state_reason}</div>
          )}
        </div>
        <div className="hp-footer">
          <span className="hp-open">{actionLabel} →</span>
          {!isIdle && <StateBadge
            state={p.state}
            attentionCount={counts.questions + counts.blockers}
            cooldownHint={p.state === "blocked_resources" ? cooldownHint : undefined}
          />}
          {p.spend_today > 0 && <span className="hp-spend">{money(p.spend_today)} today</span>}
        </div>
      </Link>
      <ProjectActions project={p} onPatch={onPatch} compact />
    </article>
  );
}

function NewProjectRow({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="home-project home-project-new" onClick={onClick}>
      <span className="hp-new-icon" aria-hidden>
        +
      </span>
      <span className="hp-main">
        <span className="hp-name">new project</span>
        <span className="hp-stats">create a project and configure its repos</span>
      </span>
    </button>
  );
}

function MachineCard({ m }: { m: OverviewMachine }) {
  return (
    <Link to="/machines" className={`cap-machine ${m.online ? "online" : "offline"}`}>
      <span className="cap-head">
        <i className={`dot ${m.online ? "" : "off"}`} />
        <span className="cap-name">{m.name}</span>
        <span className="cap-kind">{machineAvailabilityLabel(m)}</span>
      </span>
      <span className="cap-agents">
        {m.agents.length === 0 && <span className="muted">no agents discovered</span>}
        {m.agents.map((a) => (
          <span key={a.id} className={`agent-pill ${AGENT_TONE[a.status]}`}>
            <span className="mono">{a.backend}</span> {agentLabel(a)}
          </span>
        ))}
      </span>
    </Link>
  );
}

function machineAvailabilityLabel(machine: OverviewMachine): string {
  if (machine.device_kind === "server") return "cloud server";
  if (machine.device_kind === "laptop") return "personal computer";
  if (machine.id.startsWith("runner:") || machine.kind === "unlinked") return "unlinked machine";
  return "availability unknown";
}

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [repoMode, setRepoMode] = useState<"create" | "existing">("existing");
  const [repoUrl, setRepoUrl] = useState("");
  const [validationCommand, setValidationCommand] = useState("");
  const [createdId, setCreatedId] = useState("");
  const [repoCreated, setRepoCreated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Keep the accepted project ID on partial failure, so retry only finishes setup.
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (repoMode === "existing" && !repoUrl.trim()) {
      setError("paste the repo URL, or switch to 'create one for me'");
      return;
    }
    setBusy(true);
    setError("");
    let projectId = createdId;
    try {
      if (!projectId) {
        const project = await api.createProject({ name: name.trim() });
        projectId = project.id;
        setCreatedId(project.id);
      }
      if (repoMode === "create" && !repoCreated) {
        await api.createProjectRepo(projectId, { name: "", private: true });
        setRepoCreated(true);
      }
      await api.patchProject(projectId, {
        name: name.trim(), validation_command: validationCommand.trim(),
        ...(repoMode === "existing" ? { spec_repo: repoUrl.trim(), member_repos: [repoUrl.trim()] } : {}),
      });
      navigate(`/p/${projectId}`);
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <form className="modal modal-narrow" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>New project</h2>
        <p className="modal-hint">Connect a repository, then submit a plan. Agents start only after you approve the work.</p>
        <label>
          name
          <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus placeholder="atlas" />
        </label>
        <div className="repo-mode">
          <label className="repo-mode-option">
            <input
              type="radio"
              name="repo-mode"
              checked={repoMode === "create"}
              onChange={() => setRepoMode("create")}
            />
            create a private repo for me
          </label>
          <label className="repo-mode-option">
            <input
              type="radio"
              name="repo-mode"
              checked={repoMode === "existing"}
              onChange={() => setRepoMode("existing")}
            />
            use an existing repo
          </label>
          {repoMode === "existing" && (
            <label>Repository URL
              <input
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/org/project.git"
              />
            </label>
          )}
        </div>
        <label>Validation command
          <input value={validationCommand} onChange={e => setValidationCommand(e.target.value)} placeholder="make check" />
        </label>
        <p className="modal-hint">Hive runs this after review and before landing each change. Leave blank to rely on agent review.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        {createdId && error && <Link to={`/p/${createdId}`}>Open the created project</Link>}
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>
            cancel
          </button>
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : createdId ? "Retry setup" : "Create project"}
          </button>
        </div>
      </form>
    </div>
  );
}

function HomeSkeleton() {
  return (
    <div className="home-skeleton" aria-hidden>
      <div className="home-grid">
        <div className="panel skeleton tall" />
        <div className="panel skeleton tall" />
      </div>
    </div>
  );
}

export default function Home() {
  const { data, failed, refresh } = useOverview();
  const [showNew, setShowNew] = useState(false);

  if (!data) {
    return (
      <div className="page page-home">
        {failed ? <p className="muted">chief unreachable</p> : <HomeSkeleton />}
      </div>
    );
  }

  const t = data.totals;
  const cooldownHint = soonestCooldown(data);
  const activePlans = data.projects.filter(p => p.plan?.status === "approved").length;
  const draftPlans = data.projects.filter(p => p.plan?.status === "draft").length;
  const summary = [
    activePlans ? `${activePlans} ${activePlans === 1 ? "plan" : "plans"} in progress` : null,
    draftPlans ? `${draftPlans} awaiting approval` : null,
    t.needs_you > 0 ? `${t.needs_you} ${t.needs_you === 1 ? "action needs you" : "actions need you"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const togglePause = async () => {
    await api.setFleetPaused(!data.paused);
    refresh();
  };

  return (
    <div className="page page-home">
      <div className="home-head">
        <div className="home-heading">
          <h1>{data.paused ? "Hive is paused" : "Project work"}</h1>
          <p>{summary || "Submit a plan, follow its progress, and inspect the results."}</p>
        </div>
        <button
          type="button"
          className={data.paused ? "" : "ghost"}
          title="Nothing new starts anywhere; running tasks finish and report."
          onClick={togglePause}
        >
          {data.paused ? "Resume hive" : "Pause hive"}
        </button>
      </div>

      {data.paused ? (
        <div className="fleet-paused-banner">
          <i className="ti ti-player-pause" aria-hidden />{" "}
          Hive is paused — no new tasks, scans, or agent sessions start anywhere.
          Running tasks finish and report. Resume to let queued work continue.
        </div>
      ) : null}

      <div className="home-grid">
        <section className="panel">
          <div className="panel-head">
            <h2>projects</h2>
          </div>
          {data.projects.length === 0 ? (
            <p className="muted">No projects yet.</p>
          ) : null}
          <div className="home-project-list">
            {data.projects.map((p) => (
              <ProjectRow
                key={p.id}
                p={p}
                cooldownHint={cooldownHint}
                onPatch={async (patch) => {
                  await api.patchProject(p.id, patch);
                  refresh();
                }}
              />
            ))}
            <NewProjectRow onClick={() => setShowNew(true)} />
          </div>
        </section>

        <div className="home-rail">
          <section className="panel">
            <div className="panel-head">
              <h2>
                <i className="ti ti-hand-stop" aria-hidden /> needs you
              </h2>
              {data.attention.count > 0 && <span className="badge hot">{data.attention.count}</span>}
              <Link to="/needs-you" className="panel-link">
                view all →
              </Link>
            </div>
            {data.attention.count === 0 && <p className="muted">nothing needs you right now</p>}
            {data.attention.questions.map((q) => (
              <Link key={q.id} to="/needs-you" className="rail-item">
                <span className="rail-tag">{q.project_name || "project"} · question</span>
                <span className="rail-text">{q.text}</span>
              </Link>
            ))}
            {data.attention.human_todos.map((todo) => (
              <Link key={todo.id} to="/needs-you" className="rail-item">
                <span className="rail-tag">{todo.project_name || "org-wide"} · todo</span>
                <span className="rail-text">{todo.title}</span>
              </Link>
            ))}
            {(data.attention.offers ?? []).map((offer) => (
              <Link key={offer.workstream_id} to={`/p/${offer.project_id}`} className="rail-item">
                <span className="rail-tag">{offer.project_name} · hive offers</span>
                <span className="rail-text">{offer.offer}</span>
              </Link>
            ))}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>
                <i className="ti ti-bolt" aria-hidden /> live now
              </h2>
            </div>
            {data.live_tasks.length === 0 && <p className="muted">no tasks running</p>}
            {data.live_tasks.map((task) => (
              <Link key={task.id} to={`/p/${task.project_id}`} className="live-item">
                <i className="dot live" />
                <span className="live-text">
                  {task.project_name} · <span className="mono">{task.backend}</span> {task.kind}
                  {task.issue_number ? ` #${task.issue_number}` : ""}
                </span>
                <span className="live-age">{duration(task.started_at, 0)}</span>
              </Link>
            ))}
          </section>
        </div>
      </div>

      <details className="home-diagnostics">
        <summary>
          <span>Capacity &amp; activity</span>
          <span>{t.agents_ready} {t.agents_ready === 1 ? "agent" : "agents"} ready · {money(t.spend_today)} today</span>
        </summary>
        <div className="kpi-row">
          <Kpi icon="bolt" label="running" value={t.tasks_running} sub="tasks now" />
          <Kpi
            icon="robot"
            label="agents"
            value={<>{t.agents_ready}<span> / {t.agents_total}</span></>}
            sub="ready"
            to="/machines"
          />
          <Kpi
            icon="server"
            label="machines"
            value={<>{t.machines_online}<span> / {t.machines_total}</span></>}
            sub="online"
            to="/machines"
          />
          <Kpi
            icon="alert-circle"
            label="needs you"
            value={t.needs_you}
            sub={`${data.attention.questions.length} q · ${data.attention.human_todos.length} todo`}
            hot={t.needs_you > 0}
            to="/needs-you"
          />
          <Kpi
            icon="currency-dollar"
            label="spend"
            value={money(t.spend_today)}
            sub={t.budget_today > 0 ? `of ${money(t.budget_today)} today` : "today"}
          />
        </div>

        <section className="panel">
          <div className="panel-head">
            <h2>
              <i className="ti ti-cpu" aria-hidden /> capacity · machines &amp; agents
            </h2>
            <Link to="/machines" className="panel-link">
              manage →
            </Link>
          </div>
          {data.capacity.machines.length === 0 ? (
            <p className="muted">No machines enrolled. Enroll a machine on the machines page.</p>
          ) : (
            <div className="cap-list">
              {data.capacity.machines.map((m) => (
                <MachineCard key={m.id} m={m} />
              ))}
            </div>
          )}
          {data.subscriptions.length > 0 && (
            <div className="cap-subs">
              <span className="muted">subscriptions</span>
              {data.subscriptions.map((s) => (
                <span key={s.id} className="chip">
                  {s.provider}
                  {s.plan ? ` · ${s.plan}` : ""}
                </span>
              ))}
            </div>
          )}
        </section>
      </details>

      {showNew && <NewProjectModal onClose={() => setShowNew(false)} />}
    </div>
  );
}
