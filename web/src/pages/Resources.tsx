import { useEffect, useState } from "react";
import { ago, api, countdown, money, usePoll } from "../api";
import type {
  LicensingMode,
  LocalRunnerInfo,
  MachineGroup,
  MachineInfo,
  ResourceInfo,
  SubscriptionCandidate,
} from "../types";

const LICENSING_LABEL: Record<LicensingMode, string> = {
  portable: "portable",
  machine_bound: "machine-bound",
  unknown: "licensing?",
};

const LICENSING_TITLE: Record<LicensingMode, string> = {
  portable: "API key Hive can copy to any machine — Hive can stand this up itself.",
  machine_bound: "Login tied to one machine — a human must log in where it is needed.",
  unknown: "Licensing unknown. Set it so Hive knows whether it can self-serve the auth.",
};

interface ProviderLiveness {
  available: boolean; // dispatchable on some machine right now
  usable: boolean; // proven usable somewhere, even if offline/cooling
  machines: string[];
}

/** Tie each subscription to the live agents below it: which machines have a
 *  usable agent for that provider, and whether any is dispatchable now. */
function providerLiveness(cards: MachineGroup[]): Map<string, ProviderLiveness> {
  const map = new Map<string, ProviderLiveness>();
  for (const card of cards) {
    for (const res of card.resources) {
      const entry = map.get(res.backend) ?? { available: false, usable: false, machines: [] };
      if (res.usability_status === "usable") {
        entry.usable = true;
        if (!entry.machines.includes(card.machine.name)) entry.machines.push(card.machine.name);
      }
      if (res.available) entry.available = true;
      map.set(res.backend, entry);
    }
  }
  return map;
}

function liveStatus(live: ProviderLiveness | undefined) {
  if (live?.available) {
    return (
      <span className="avail ok" title={`dispatchable now on ${live.machines.join(", ")}`}>
        live · {live.machines.join(", ")}
      </span>
    );
  }
  if (live?.usable) {
    return (
      <span className="avail cool" title={`authenticated on ${live.machines.join(", ")} but not dispatchable right now`}>
        set up · idle
      </span>
    );
  }
  return (
    <span className="avail wait" title="No machine has a usable agent for this subscription yet. Hive will ask you to log one in where it needs capacity.">
      needs setup
    </span>
  );
}

function licensingChip(mode: LicensingMode) {
  return (
    <span className={`chip licensing-${mode}`} title={LICENSING_TITLE[mode]}>
      {LICENSING_LABEL[mode]}
    </span>
  );
}

function Subscriptions({
  candidates,
  cards,
  onChanged,
}: {
  candidates: SubscriptionCandidate[];
  cards: MachineGroup[];
  onChanged: () => void;
}) {
  const { data, refresh } = usePoll(() => api.subscriptions(), []);
  const [provider, setProvider] = useState("");
  const [plan, setPlan] = useState("");
  const [licensing, setLicensing] = useState<LicensingMode>("unknown");
  const [notes, setNotes] = useState("");
  if (!data) return null;

  const live = providerLiveness(cards);
  const have = new Set(data.map((s) => s.provider));
  const detected = candidates.filter((c) => !have.has(c.provider));

  const reload = () => Promise.all([refresh(), Promise.resolve(onChanged())]);
  const add = async (p: string, pl: string, lic: LicensingMode, nt: string) => {
    if (!p.trim()) return;
    await api.addSubscription(p.trim(), pl.trim(), lic, nt.trim());
    setProvider("");
    setPlan("");
    setLicensing("unknown");
    setNotes("");
    await reload();
  };

  return (
    <section className="subscriptions">
      <h2 className="col-title">subscriptions</h2>
      <p className="muted">
        The AI agents you have access to — your durable capacity. Hive installs and probes them per
        machine under “machines &amp; agents” below.
      </p>
      <table className="res-table sub-table">
        <thead>
          <tr>
            <th>agent</th>
            <th>plan</th>
            <th>licensing</th>
            <th>status</th>
            <th>notes</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((s) => {
            const mode = (s.licensing_mode ?? "unknown") as LicensingMode;
            return (
              <tr key={s.id}>
                <td className="mono">{s.provider}</td>
                <td>{s.plan || "—"}</td>
                <td>{licensingChip(mode)}</td>
                <td>{liveStatus(live.get(s.provider))}</td>
                <td className="muted">{s.notes || "—"}</td>
                <td className="num">
                  <button
                    className="ghost"
                    title="remove"
                    onClick={async () => {
                      await api.deleteSubscription(s.id);
                      await reload();
                    }}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            );
          })}
          {data.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">
                no subscriptions recorded
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {detected.length > 0 && (
        <div className="sub-candidates">
          <h3 className="sub-candidates-title">detected on your machines</h3>
          <p className="muted">
            Agents already proven usable but not recorded yet. Confirm to track them as durable
            subscriptions.
          </p>
          {detected.map((c) => (
            <div key={c.provider} className="candidate-row">
              <span className="mono">{c.provider}</span>
              {licensingChip(c.licensing_mode)}
              <span className="muted">{c.evidence}</span>
              <button className="ghost" onClick={() => add(c.provider, "", c.licensing_mode, "")}>
                add
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="sub-add">
        <input
          placeholder="agent (codex / claude / cursor / gemini-cli)"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
        />
        <input placeholder="plan (e.g. ChatGPT Plus)" value={plan} onChange={(e) => setPlan(e.target.value)} />
        <select
          value={licensing}
          title="how the credential is licensed"
          onChange={(e) => setLicensing(e.target.value as LicensingMode)}
        >
          <option value="unknown">licensing…</option>
          <option value="portable">portable (API key)</option>
          <option value="machine_bound">machine-bound (login)</option>
        </select>
        <input placeholder="notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <button onClick={() => add(provider, plan, licensing, notes)} disabled={!provider.trim()}>
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

  const runnerOnline = (id: string) =>
    data?.cards.some((card) => card.runners.some((r) => r.id === id && r.online)) ?? false;

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

  const forgetMachine = async (id: string) => {
    setRunnerError("");
    try {
      await api.forgetMachine(id);
      await refresh();
    } catch {
      setRunnerError("could not forget this machine");
    }
  };

  const startLocalRunner = async () => {
    setStartingRunner(true);
    setRunnerError("");
    try {
      await api.startLocalRunner();
      await refresh();
    } catch {
      setRunnerError("could not enroll this machine");
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
      setRunnerError("could not update auto-enroll setting");
    } finally {
      setUpdatingAutostart(false);
    }
  };

  const localRunner = data?.local_runner;
  const machineCards = data?.cards ?? [];

  return (
    <div className="page page-resources">
      <div className="page-head">
        <h1>Machines</h1>
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
          <Subscriptions
            candidates={data.subscription_candidates}
            cards={machineCards}
            onChanged={refresh}
          />
          <h2 className="col-title">machines &amp; agents</h2>
          <p className="muted">
            Where each subscription is installed and dispatchable right now. Secondary to your
            subscriptions above — a subscription with no online machine is a setup task, not lost
            capacity.
          </p>
          <div className="runner-grid machine-grid">
            {machineCards.length === 0 && (
              <section className="runner-empty">
                <h2>No machines enrolled</h2>
                <p className="muted">
                  Enroll this machine so Hive can discover local agent CLIs and queue probes.
                </p>
                {localRunner?.supported ? (
                  <button onClick={startLocalRunner} disabled={startingRunner}>
                    {startingRunner ? "starting" : "enroll this machine"}
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
                onForget={forgetMachine}
              />
            ))}
          </div>
        </>
      )}

      <OrgContext />
    </div>
  );
}

function MachineCard({
  card,
  probing,
  updatingResource,
  runnerOnline,
  onProbe,
  onSetEnabled,
  onForget,
}: {
  card: MachineGroup;
  probing: string | null;
  updatingResource: string | null;
  runnerOnline: (id: string) => boolean;
  onProbe: (id: string) => void;
  onSetEnabled: (res: ResourceInfo, enabled: boolean) => void;
  onForget: (id: string) => void;
}) {
  const { online, last_seen: lastSeen } = card;
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
        <div className="machine-status">
          <span className="runner-seen">
            {online ? "online" : lastSeen > 0 ? `last seen ${ago(lastSeen)}` : "offline"}
          </span>
          {!online && (
            <button
              className="ghost quiet machine-forget"
              title="Forget this machine and its agents. A live runner re-registers automatically."
              onClick={() => {
                if (confirm(`Forget ${card.machine.name}? Its agents and history are removed. A running runner will re-register.`)) {
                  onForget(card.machine.id);
                }
              }}
            >
              forget
            </button>
          )}
        </div>
      </header>

      <div className="machine-meta">
        <span className="chip" title={machineTypeTitle(card.machine)}>
          {machineTypeLabel(card.machine)}
        </span>
        <span className="chip" title={deviceKindTitle(card.machine.device_kind)}>
          {deviceKindLabel(card.machine.device_kind)}
        </span>
        {isUnlinkedMachine(card.machine) && (
          <span className="chip" title="This capacity is visible, but Hive has not linked it to a durable machine record.">
            unlinked machine
          </span>
        )}
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
  if (kind === "server") return "cloud server";
  if (kind === "laptop") return "personal computer";
  return "availability unknown";
}

function deviceKindTitle(kind: string): string {
  if (kind === "server") return "Expected to stay online unless explicitly stopped.";
  if (kind === "laptop") return "May sleep, disconnect, or move between networks.";
  return "Set HIVE_MACHINE_KIND=server or HIVE_MACHINE_KIND=laptop to make availability expectations explicit.";
}

function isUnlinkedMachine(machine: MachineInfo): boolean {
  return machine.id.startsWith("runner:") || machine.kind === "unlinked";
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
      <label className="runner-autostart" title="enroll this machine automatically with hive run">
        <input
          type="checkbox"
          checked={localRunner.autostart}
          disabled={busy}
          onChange={(event) => onAutostart(event.currentTarget.checked)}
        />
        <span>auto-enroll on launch</span>
      </label>
      {localRunner.registered ? (
        <span className="local-runner-chip" title={localRunner.message || localRunner.log_path}>
          this machine enrolled
        </span>
      ) : (
        <button className="ghost" onClick={onStart} disabled={busy}>
          {busy ? "starting" : "enroll this machine"}
        </button>
      )}
    </div>
  );
}
