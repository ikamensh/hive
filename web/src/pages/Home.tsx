import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, countdown, duration, money, repoShort } from "../api";
import { useOverview } from "../App";
import { StateBadge } from "../components/shared";
import { ProjectActions } from "../features/project/controls";
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
  const { counts } = p;
  const bits = [
    `${counts.active} active`,
    counts.running ? `${counts.running} running` : null,
    counts.blockers ? `${counts.blockers} blockers` : null,
    `${counts.streams} streams`,
  ].filter(Boolean);
  return (
    <article className="home-project">
      <Link to={`/p/${p.id}`} className="hp-content">
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
          <div className="hp-stats">{bits.join(" · ")}</div>
        </div>
        <StateBadge
          state={p.state}
          attentionCount={counts.questions + counts.blockers}
          cooldownHint={p.state === "blocked_resources" ? cooldownHint : undefined}
        />
        <span
          className={`hp-spend${p.spend_today > 0 ? "" : " hp-spend-empty"}`}
          title="spend today"
        >
          {p.spend_today > 0 ? money(p.spend_today) : "$0"}
        </span>
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const p = await api.createProject({ name: name.trim() });
      navigate(`/p/${p.id}`);
    } catch {
      setError("create failed — is the chief up?");
      setBusy(false);
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <form className="modal modal-narrow" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>New project</h2>
        <p className="modal-hint">Configure repos and goals on the project page.</p>
        <label>
          name
          <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus placeholder="atlas" />
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

function HomeSkeleton() {
  return (
    <div className="home-skeleton" aria-hidden>
      <div className="kpi-row">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="kpi skeleton" />
        ))}
      </div>
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
  const summary = [
    `${t.tasks_running} ${t.tasks_running === 1 ? "task" : "tasks"} running`,
    t.needs_you > 0 ? `${t.needs_you} ${t.needs_you === 1 ? "thing needs you" : "things need you"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="page page-home">
      <div className="home-head">
        <h1>{summary || "the hive is quiet"}</h1>
      </div>

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

      {showNew && <NewProjectModal onClose={() => setShowNew(false)} />}
    </div>
  );
}
