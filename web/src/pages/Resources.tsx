import { useEffect, useState } from "react";
import { marked } from "marked";
import { ago, api, countdown, money, usePoll } from "../api";
import type { LocalRunnerInfo, MachineInfo, ResourceInfo, ResourcesPayload, RunnerInfo } from "../types";

function HumanTasks() {
  const { data, refresh } = usePoll(() => api.humanTasks(), []);
  const { data: projects } = usePoll(() => api.projects(), [], 30000);
  if (!data) return null;
  const open = data.filter((t) => t.status === "open");
  const done = data.filter((t) => t.status === "done");
  const scopeTag = (projectId: string) =>
    projectId === "" ? "org-wide" : (projects?.find((p) => p.id === projectId)?.name ?? projectId);
  return (
    <section className="human-tasks">
      <h2 className="col-title">
        your todos {open.length > 0 && <span className="badge hot">{open.length}</span>}
      </h2>
      <p className="muted">Things only a human can do: logins, auth refresh, infra unblocks.</p>
      {open.length === 0 && <p className="muted">nothing needs you right now</p>}
      {open.map((t) => (
        <article key={t.id} className="todo-card">
          <header>
            <h3>{t.title}</h3>
            <span className="chip">{scopeTag(t.project_id)}</span>
            <span className="muted">{ago(t.created_at)}</span>
          </header>
          <div className="md" dangerouslySetInnerHTML={{ __html: marked.parse(t.instructions) as string }} />
          <div className="org-actions">
            <button
              onClick={async () => {
                await api.completeHumanTask(t.id);
                refresh();
              }}
            >
              mark done
            </button>
          </div>
        </article>
      ))}
      {done.length > 0 && (
        <details className="answered-fold">
          <summary>{done.length} done</summary>
          {done.map((t) => (
            <div key={t.id} className="answered-row">
              <span>{t.title}</span>
              <span className="muted">{ago(t.done_at)}</span>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}

function Subscriptions() {
  const { data, refresh } = usePoll(() => api.subscriptions(), []);
  const [provider, setProvider] = useState("");
  const [plan, setPlan] = useState("");
  const [notes, setNotes] = useState("");
  if (!data) return null;
  const add = async () => {
    if (!provider.trim()) return;
    await api.addSubscription(provider.trim(), plan.trim(), notes.trim());
    setProvider("");
    setPlan("");
    setNotes("");
    refresh();
  };
  return (
    <section className="subscriptions">
      <h2 className="col-title">subscriptions</h2>
      <p className="muted">
        AI plans you own. Hive uses this to know what capacity exists and where logins are needed.
      </p>
      <table className="res-table">
        <thead>
          <tr>
            <th>backend</th>
            <th>plan</th>
            <th>notes</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((s) => (
            <tr key={s.id}>
              <td className="mono">{s.provider}</td>
              <td>{s.plan || "—"}</td>
              <td className="muted">{s.notes || "—"}</td>
              <td className="num">
                <button
                  className="ghost"
                  title="remove"
                  onClick={async () => {
                    await api.deleteSubscription(s.id);
                    refresh();
                  }}
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
          {data.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                no subscriptions recorded
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="sub-add">
        <input placeholder="backend (codex / claude / cursor / gemini-cli)" value={provider} onChange={(e) => setProvider(e.target.value)} />
        <input placeholder="plan (e.g. ChatGPT Plus)" value={plan} onChange={(e) => setPlan(e.target.value)} />
        <input placeholder="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <button onClick={add} disabled={!provider.trim()}>
          add
        </button>
      </div>
    </section>
  );
}

function OrgContext() {
  const [text, setText] = useState<string | null>(null);
  const [saved, setSaved] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    api
      .orgContext()
      .then(setText)
      .catch(() => setError(true));
  }, []);

  const save = async () => {
    if (text === null) return;
    try {
      await api.setOrgContext(text);
      setSaved(true);
      setError(false);
    } catch {
      setError(true);
    }
  };

  return (
    <section className="org-context">
      <h2 className="col-title">org context</h2>
      <p className="muted">Shared with every supervisor and agent across all projects.</p>
      {text === null ? (
        <p className="muted">{error ? "unreachable" : "loading…"}</p>
      ) : (
        <>
          <textarea
            value={text}
            rows={8}
            onChange={(e) => {
              setText(e.target.value);
              setSaved(false);
            }}
          />
          <div className="org-actions">
            {error && <span className="form-error">save failed</span>}
            <button onClick={save} disabled={saved}>
              {saved ? "saved" : "save"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export default function Resources() {
  const { data, failed, refresh } = usePoll(() => api.resources(), []);
  const [probing, setProbing] = useState<string | null>(null);
  const [updatingResource, setUpdatingResource] = useState<string | null>(null);
  const [startingRunner, setStartingRunner] = useState(false);
  const [updatingAutostart, setUpdatingAutostart] = useState(false);
  const [runnerError, setRunnerError] = useState("");
  // 1s ticker so cooldown countdowns feel live between polls.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const runnerOnline = (id: string) => data?.runners.find((r) => r.id === id)?.online ?? false;

  const probe = async (id: string) => {
    setProbing(id);
    try {
      await api.probeResource(id);
      await refresh();
    } finally {
      setProbing(null);
    }
  };

  const setResourceEnabled = async (res: ResourceInfo, enabled: boolean) => {
    setUpdatingResource(res.id);
    try {
      await api.updateResource(res.id, {
        enabled,
        disabled_reason: enabled ? "" : "No subscription or intentionally not used.",
      });
      await refresh();
    } finally {
      setUpdatingResource(null);
    }
  };

  const startLocalRunner = async () => {
    setStartingRunner(true);
    setRunnerError("");
    try {
      await api.startLocalRunner();
      await refresh();
    } catch {
      setRunnerError("could not start local runner");
    } finally {
      setStartingRunner(false);
    }
  };

  const updateLocalRunnerAutostart = async (autostart: boolean) => {
    setUpdatingAutostart(true);
    setRunnerError("");
    try {
      await api.updateLocalRunner({ autostart });
      await refresh();
    } catch {
      setRunnerError("could not update local runner autostart");
    } finally {
      setUpdatingAutostart(false);
    }
  };

  const localRunner = data?.local_runner;
  const machineCards = data ? buildMachineCards(data) : [];

  return (
    <div className="page page-resources">
      <div className="page-head">
        <h1>Resources</h1>
        {localRunner?.supported && (
          <LocalRunnerAction
            localRunner={localRunner}
            busy={startingRunner || updatingAutostart}
            onStart={startLocalRunner}
            onAutostart={updateLocalRunnerAutostart}
          />
        )}
      </div>
      {!data && <p className="muted">{failed ? "unreachable" : "loading…"}</p>}

      {data && (
        <>
          {runnerError && <p className="form-error runner-error">{runnerError}</p>}
          <h2 className="col-title">machines & agents</h2>
          <div className="runner-grid machine-grid">
            {machineCards.length === 0 && (
              <section className="runner-empty">
                <h2>No runners registered</h2>
                <p className="muted">
                  Start a runner on this control-plane host to discover local agent CLIs and queue probes.
                </p>
                {localRunner?.supported ? (
                  <button onClick={startLocalRunner} disabled={startingRunner}>
                    {startingRunner ? "starting" : "enroll this host"}
                  </button>
                ) : (
                  <code>uv run python -m hive.runner</code>
                )}
              </section>
            )}
            {machineCards.map((card) => (
              <MachineCard
                key={card.machine.id}
                card={card}
                probing={probing}
                updatingResource={updatingResource}
                runnerOnline={runnerOnline}
                onProbe={probe}
                onSetEnabled={setResourceEnabled}
              />
            ))}
          </div>
        </>
      )}

      <HumanTasks />
      <Subscriptions />
      <OrgContext />
    </div>
  );
}

interface MachineCardData {
  machine: MachineInfo;
  runners: RunnerInfo[];
  resources: ResourceInfo[];
}

function buildMachineCards(data: ResourcesPayload): MachineCardData[] {
  const machines = data.machines ?? [];
  const machineIds = new Set(machines.map((m) => m.id));
  const resourcesById = new Set<string>();
  const cards = machines.map((machine) => {
    const runners = data.runners.filter((runner) => runner.machine_id === machine.id);
    const runnerIds = new Set(runners.map((runner) => runner.id));
    const resources = data.resources.filter((res) => res.machine_id === machine.id || runnerIds.has(res.runner_id));
    resources.forEach((res) => resourcesById.add(res.id));
    return { machine, runners, resources };
  });

  for (const runner of data.runners) {
    if (runner.machine_id && machineIds.has(runner.machine_id)) continue;
    const machine = virtualMachineForRunner(runner);
    const resources = data.resources.filter((res) => res.runner_id === runner.id);
    resources.forEach((res) => resourcesById.add(res.id));
    cards.push({ machine, runners: [runner], resources });
  }

  const orphanResources = data.resources.filter((res) => !resourcesById.has(res.id));
  if (orphanResources.length > 0) {
    cards.push({
      machine: {
        id: "unassigned-resources",
        workspace_id: "",
        name: "unassigned",
        hostname: "",
        kind: "unknown",
        machine_type: "",
        os: "",
        arch: "",
        device_kind: "unknown",
        first_seen: 0,
        last_seen: 0,
      },
      runners: [],
      resources: orphanResources,
    });
  }

  return cards;
}

function virtualMachineForRunner(runner: RunnerInfo): MachineInfo {
  return {
    id: `runner:${runner.id}`,
    workspace_id: runner.workspace_id ?? "",
    name: runner.name,
    hostname: runner.name,
    kind: "runner",
    machine_type: "",
    os: "",
    arch: "",
    device_kind: "unknown",
    first_seen: runner.last_seen,
    last_seen: runner.last_seen,
  };
}

function MachineCard({
  card,
  probing,
  updatingResource,
  runnerOnline,
  onProbe,
  onSetEnabled,
}: {
  card: MachineCardData;
  probing: string | null;
  updatingResource: string | null;
  runnerOnline: (id: string) => boolean;
  onProbe: (id: string) => void;
  onSetEnabled: (res: ResourceInfo, enabled: boolean) => void;
}) {
  const online = card.runners.some((runner) => runner.online);
  const lastSeen = Math.max(card.machine.last_seen, ...card.runners.map((runner) => runner.last_seen), 0);
  const runnerNames = card.runners.map((runner) => runner.name).join(", ");
  const runnerById = new Map(card.runners.map((runner) => [runner.id, runner.name]));

  return (
    <article className={`runner-card machine-card ${online ? "online" : "offline"}`}>
      <header>
        <div className="machine-title">
          <i className="dot" />
          <div>
            <h3>{card.machine.name}</h3>
            {card.machine.hostname && card.machine.hostname !== card.machine.name && (
              <span className="machine-host">{card.machine.hostname}</span>
            )}
          </div>
        </div>
        <span className="runner-seen">
          {online ? "runner online" : lastSeen > 0 ? `last seen ${ago(lastSeen)}` : "offline"}
        </span>
      </header>

      <div className="machine-meta">
        <span className="chip" title={machineTypeTitle(card.machine)}>
          {machineTypeLabel(card.machine)}
        </span>
        <span className="chip" title={deviceKindTitle(card.machine.device_kind)}>
          {deviceKindLabel(card.machine.device_kind)}
        </span>
        <span className="chip" title={runnerNames ? `runner process: ${runnerNames}` : "no runner process online"}>
          {card.machine.kind || "unknown"}
        </span>
      </div>

      {card.resources.length > 0 ? (
        <table className="res-table machine-agent-table">
          <thead>
            <tr>
              <th>agent</th>
              <th>discovery</th>
              <th>usability</th>
              <th>availability</th>
              <th className="num">tasks</th>
              <th className="num">cost</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {card.resources.map((res) => (
              <tr key={res.id} className={isResourceEnabled(res) ? undefined : "resource-disabled"}>
                <td className="mono">
                  <span>{res.backend}</span>
                  {card.runners.length > 1 && runnerById.get(res.runner_id) && (
                    <span className="runner-inline">{runnerById.get(res.runner_id)}</span>
                  )}
                </td>
                <td>
                  <span className={`probe-status discovery-${res.discovery_status}`} title={discoveryTitle(res)}>
                    {res.discovery_status}
                  </span>
                  {res.cli_version && <span className="probe-age">{res.cli_version}</span>}
                </td>
                <td>
                  <span className={`probe-status probe-${res.usability_status}`} title={usabilityTitle(res)}>
                    {res.usability_status === "unknown" ? "probe required" : res.usability_status}
                  </span>
                </td>
                <td>{availability(res)}</td>
                <td className="num">{res.total_tasks}</td>
                <td className="num">{money(res.total_cost_usd)}</td>
                <td className="num">
                  <div className="resource-actions">
                    {isResourceEnabled(res) ? (
                      <>
                        <button
                          className="ghost"
                          disabled={!runnerOnline(res.runner_id) || res.usability_status === "probing" || probing === res.id}
                          onClick={() => onProbe(res.id)}
                        >
                          {probing === res.id || res.usability_status === "probing" ? "probing" : "probe"}
                        </button>
                        <button
                          className="ghost quiet"
                          title="mark this agent intentionally unavailable on this machine"
                          disabled={updatingResource === res.id || res.usability_status === "probing"}
                          onClick={() => onSetEnabled(res, false)}
                        >
                          disable
                        </button>
                      </>
                    ) : (
                      <button
                        className="ghost"
                        title={res.disabled_reason || "resource is disabled"}
                        disabled={updatingResource === res.id}
                        onClick={() => onSetEnabled(res, true)}
                      >
                        enable
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted machine-empty">no agent CLIs discovered for this machine</p>
      )}
    </article>
  );
}

function machineTypeLabel(machine: MachineInfo): string {
  const type = machine.machine_type || machine.os || "unknown";
  return [type, machine.arch].filter(Boolean).join(" ");
}

function machineTypeTitle(machine: MachineInfo): string {
  const parts = [
    machine.machine_type && `type: ${machine.machine_type}`,
    machine.os && `os: ${machine.os}`,
    machine.arch && `arch: ${machine.arch}`,
  ].filter(Boolean);
  return parts.join("\n") || "machine type unknown";
}

function deviceKindLabel(kind: string): string {
  if (kind === "server") return "server";
  if (kind === "laptop") return "laptop";
  return "availability unknown";
}

function deviceKindTitle(kind: string): string {
  if (kind === "server") return "Expected to stay online unless explicitly stopped.";
  if (kind === "laptop") return "May sleep, disconnect, or move between networks.";
  return "Set HIVE_MACHINE_KIND=server or HIVE_MACHINE_KIND=laptop to make availability expectations explicit.";
}

function discoveryTitle(res: ResourceInfo): string {
  const checked = res.discovered_at > 0 ? `discovered ${ago(res.discovered_at)}` : "not checked yet";
  return [checked, res.cli_path, res.discovery_text].filter(Boolean).join("\n\n");
}

function usabilityTitle(res: ResourceInfo): string {
  const checked = res.last_probe_at > 0 ? `last checked ${ago(res.last_probe_at)}` : "not probed yet";
  return [
    isResourceEnabled(res) ? "" : `disabled: ${res.disabled_reason || "No reason recorded."}`,
    checked,
    res.last_probe_task_id && `task ${res.last_probe_task_id}`,
    res.last_probe_text,
  ].filter(Boolean).join("\n\n");
}

function exhaustionRetryHint(text: string): string | undefined {
  const match = text.match(/try again at ([^.\n]+)/i);
  return match?.[1]?.trim();
}

function exhaustionTitle(res: ResourceInfo): string {
  const parts = [
    res.last_exhaustion_text,
    res.cooldown_until > Date.now() / 1000 && `Resumes in ${countdown(res.cooldown_until)}`,
    res.last_exhaustion_at > 0 && `Hit ${ago(res.last_exhaustion_at)}`,
    res.last_exhaustion_task_id && `Task ${res.last_exhaustion_task_id}`,
  ].filter(Boolean);
  return parts.join("\n\n");
}

function availability(res: ResourceInfo) {
  if (!isResourceEnabled(res)) {
    return <span className="avail wait" title={res.disabled_reason || "resource is disabled"}>disabled</span>;
  }
  if (res.available) return <span className="avail ok">available</span>;
  if (res.cooldown_until > Date.now() / 1000) {
    const retryHint = res.last_exhaustion_text ? exhaustionRetryHint(res.last_exhaustion_text) : undefined;
    const label = res.last_exhaustion_text
      ? retryHint
        ? `quota exhausted · ${retryHint}`
        : `quota exhausted · ${countdown(res.cooldown_until)}`
      : `cooldown ${countdown(res.cooldown_until)}`;
    return (
      <span className="avail cool" title={res.last_exhaustion_text ? exhaustionTitle(res) : undefined}>
        {label}
      </span>
    );
  }
  return <span className="avail wait">not dispatchable</span>;
}

function isResourceEnabled(res: ResourceInfo): boolean {
  return res.enabled !== false;
}

function LocalRunnerAction({
  localRunner,
  busy,
  onStart,
  onAutostart,
}: {
  localRunner: LocalRunnerInfo;
  busy: boolean;
  onStart: () => void;
  onAutostart: (autostart: boolean) => void;
}) {
  return (
    <div className="local-runner-controls">
      <label className="runner-autostart" title="start this host's runner automatically with hive run">
        <input
          type="checkbox"
          checked={localRunner.autostart}
          disabled={busy}
          onChange={(event) => onAutostart(event.currentTarget.checked)}
        />
        <span>auto-start runner</span>
      </label>
      {localRunner.registered ? (
        <span className="local-runner-chip" title={localRunner.message || localRunner.log_path}>
          this host enrolled
        </span>
      ) : (
        <button className="ghost" onClick={onStart} disabled={busy}>
          {busy ? "starting" : "enroll this host"}
        </button>
      )}
    </div>
  );
}
