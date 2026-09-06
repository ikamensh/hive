import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { IconDeviceLaptop, IconServer } from "@tabler/icons-react";
import { useOverview } from "../App";
import { ago, api, countdown, money, usePoll } from "../api";
import { UsageLimits } from "../features/capacity/UsageLimits";
import "./capacity.css";
import type {
  EnrollToken,
  LicensingMode,
  LocalRunnerInfo,
  MachineGroup,
  MachineInfo,
  OverviewMachine,
  ResourceInfo,
  SubscriptionCandidate,
  WorkspaceMember,
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
  usableMachines: string[];
  availableMachines: string[];
}

/** Tie each subscription to the live agents below it: which machines have a
 *  usable agent for that provider, and whether any is dispatchable now. */
function providerLiveness(cards: MachineGroup[]): Map<string, ProviderLiveness> {
  const map = new Map<string, ProviderLiveness>();
  for (const card of cards) {
    for (const res of card.resources) {
      const entry = map.get(res.backend) ?? { usableMachines: [], availableMachines: [] };
      if (res.usability_status === "usable" && !entry.usableMachines.includes(card.machine.name)) {
        entry.usableMachines.push(card.machine.name);
      }
      if (res.available && !entry.availableMachines.includes(card.machine.name)) entry.availableMachines.push(card.machine.name);
      map.set(res.backend, entry);
    }
  }
  return map;
}

function liveStatus(live: ProviderLiveness | undefined) {
  if (live?.availableMachines.length) {
    return (
      <span className="avail ok" title="Usable on an online runner. Running work may occupy that runner.">
        usable · {live.availableMachines.join(", ")}
      </span>
    );
  }
  if (live?.usableMachines.length) {
    return (
      <span className="avail cool" title={`Previously authenticated on ${live.usableMachines.join(", ")}; currently offline or unavailable.`}>
        currently unavailable
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
  loginById,
  onChanged,
}: {
  candidates: SubscriptionCandidate[];
  cards: MachineGroup[];
  loginById: Map<string, string>;
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
      <h2 className="col-title">Subscriptions</h2>
      <p className="muted">
        Record the plans you own and how their credentials can be used. Usage readings remain
        separate for each provider and machine.
      </p>
      <table className="res-table sub-table">
        <thead>
          <tr>
            <th>agent</th>
            <th>plan</th>
            <th>licensing</th>
            <th>owner</th>
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
                <td className="muted">
                  {s.owner_user_id ? `@${loginById.get(s.owner_user_id) ?? s.owner_user_id}` : "shared"}
                </td>
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
              <td colSpan={7} className="muted">
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

// Public repo — the enroll snippet can include the clone verbatim (the mac
// installer hardcodes the same remote).
const HIVE_REPO = "https://github.com/ikamensh/hive";

function AddLaptop() {
  const [enroll, setEnroll] = useState<EnrollToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);

  const snippet = enroll
    ? `git clone ${HIVE_REPO} && cd hive\n${enroll.command}`
    : "";

  const mint = async () => {
    setBusy(true);
    setError(false);
    try {
      setEnroll(await api.createEnrollToken());
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    await navigator.clipboard.writeText(snippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!enroll) {
    return (
      <div className="enroll-cta">
        <button
          className="ghost"
          onClick={mint}
          disabled={busy}
          title="Get a one-hour command that installs the runner on another machine and claims it for you."
        >
          {busy ? "Preparing command…" : "Add a machine"}
        </button>
        {error && <span className="form-error">could not mint a token</span>}
      </div>
    );
  }
  return (
    <section className="enroll-panel">
      <h2 className="col-title">Connect a machine</h2>
      <p className="muted">
        On the laptop (needs <code>git</code> and <code>uv</code>), run:
      </p>
      <pre className="enroll-command">
        <code>{snippet}</code>
      </pre>
      <div className="enroll-actions">
        <button onClick={copy}>{copied ? "copied" : "copy command"}</button>
        <button className="ghost quiet" onClick={() => setEnroll(null)}>
          dismiss
        </button>
      </div>
      <p className="muted">
        The token is valid for {Math.round(enroll.expires_in_s / 60)} minutes and enrolls the
        machine as <b>yours</b> — its login todos will land in your inbox. If the chief sits
        behind the site password, prefix the command with{" "}
        <code>HIVE_BASIC_AUTH=user:password</code>.
      </p>
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
  const overview = useOverview();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "limits" ? "limits" : "machines";
  const setTab = (tab: "machines" | "limits") => setSearchParams(tab === "limits" ? { tab } : {});
  const { data, failed, refresh } = usePoll(() => api.resources(), []);
  const { data: members, refresh: refreshMembers } = usePoll(() => api.users(), [], 30000);
  const { data: auth } = usePoll(() => api.me(), [], 30000);
  const [probing, setProbing] = useState<string | null>(null);
  const [updatingResource, setUpdatingResource] = useState<string | null>(null);
  const [startingRunner, setStartingRunner] = useState(false);
  const [updatingAutostart, setUpdatingAutostart] = useState(false);
  const [runnerError, setRunnerError] = useState("");
  const [enrolling, setEnrolling] = useState(false);
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
    setRunnerError("");
    try {
      await api.probeResource(id);
      await refresh();
    } catch (error) {
      setRunnerError(String(error));
    } finally {
      setProbing(null);
    }
  };

  const setResourceEnabled = async (res: ResourceInfo, enabled: boolean) => {
    setUpdatingResource(res.id);
    setRunnerError("");
    try {
      await api.updateResource(res.id, {
        enabled,
        disabled_reason: enabled ? "" : "No subscription or intentionally not used.",
      });
      await refresh();
    } catch (error) {
      setRunnerError(String(error));
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

  const setMachineOwner = async (machineId: string, ownerUserId: string) => {
    setRunnerError("");
    try {
      await api.setMachineOwner(machineId, ownerUserId);
      await Promise.all([refresh(), refreshMembers()]);
    } catch {
      setRunnerError("could not change the machine owner");
    }
  };

  const localRunner = data?.local_runner;
  const machineCards = data?.cards ?? [];
  const loginById = new Map((members ?? []).map((m) => [m.user.id, m.user.github_login]));
  const myId = auth?.user.id ?? "";
  const isAdmin = auth?.role !== "resource_provider";
  const capacity = overview.failed ? undefined : overview.data?.capacity;
  const readings = machineCards.flatMap(card => card.resources.map(resource => ({
    ...resource, machineName: card.machine.name,
  })));

  return (
    <div className="page page-resources page-capacity">
      <div className="capacity-heading">
        <div><p className="capacity-eyebrow">Workspace resources</p><h1>Capacity</h1>
          <p className="muted">Machines, agent access and provider limits.</p></div>
        <button type="button" className="ghost" aria-expanded={enrolling}
          onClick={() => setEnrolling(!enrolling)}>{enrolling ? "Close setup" : "Connect a machine"}</button>
      </div>
      {enrolling && <section className="capacity-connect">
        {localRunner?.supported && <LocalRunnerAction localRunner={localRunner}
          busy={startingRunner || updatingAutostart} onStart={startLocalRunner}
          onAutostart={updateLocalRunnerAutostart} />}
        <AddLaptop />
      </section>}
      {failed && <p className="form-error" role="alert">Could not refresh machines. {data ? "The details below may be out of date." : "Retrying…"}</p>}
      {runnerError && <p className="form-error runner-error" role="alert">{runnerError}</p>}
      {!data && !failed && <p className="muted">Loading capacity…</p>}

      {data && (
        <>
          <div className="capacity-summary">
            <div><span>Machines online</span><strong>{capacity ? `${capacity.machines_online} / ${capacity.machines_total}` : "—"}</strong></div>
            <div><span>Busy runners</span><strong>{capacity?.runners_busy ?? "—"}</strong></div>
            <div aria-label="Spare execution capacity"><span>Spare runners</span><strong>{capacity?.runners_idle ?? "—"}</strong></div>
            <div><span>Usable agent choices</span><strong>{capacity?.agents_ready ?? "—"}</strong></div>
          </div>
          <p className="capacity-accounting muted">One runner executes one task at a time. Agent choices share its slot; project requirements can restrict where work runs.</p>
          <nav className="capacity-tabs" aria-label="Capacity views">
            <button type="button" className={tab === "machines" ? "selected" : "ghost"}
              aria-pressed={tab === "machines"} onClick={() => setTab("machines")}>Machines</button>
            <button type="button" className={tab === "limits" ? "selected" : "ghost"}
              aria-pressed={tab === "limits"} onClick={() => setTab("limits")}>Provider limits</button>
          </nav>
          {tab === "limits" ? <section className="capacity-limits" aria-label="Provider limits">
            <h2>Provider limits</h2><p className="muted">Reported account usage, with each observation kept separate.</p>
            <UsageLimits readings={readings} />
          </section> : <div className="capacity-machines">
            {machineCards.length === 0 && (
              <section className="capacity-empty">
                <h2>No machines enrolled</h2>
                <p className="muted">
                  Enroll this machine so Hive can discover local agent CLIs and queue probes.
                </p>
                {localRunner?.supported ? (
                  <button onClick={startLocalRunner} disabled={startingRunner}>
                    {startingRunner ? "starting" : "enroll this machine"}
                  </button>
                ) : (
                  <button type="button" onClick={() => setEnrolling(true)}>Connect your first machine</button>
                )}
              </section>
            )}
            {machineCards.map((card) => (
              <MachineCard
                key={card.machine.id}
                card={card}
                activity={capacity?.machines.find(machine => machine.id === card.machine.id)}
                probing={probing}
                updatingResource={updatingResource}
                runnerOnline={runnerOnline}
                members={members ?? []}
                myId={myId}
                isAdmin={isAdmin}
                onProbe={probe}
                onSetEnabled={setResourceEnabled}
                onForget={forgetMachine}
                onSetOwner={setMachineOwner}
              />
            ))}
          </div>}
          <details className="capacity-administration">
            <summary>Manage subscriptions</summary>
            <div className="capacity-admin-content"><Subscriptions candidates={data.subscription_candidates}
              cards={machineCards} loginById={loginById} onChanged={refresh} /></div>
          </details>
        </>
      )}
      <details className="capacity-administration"><summary>Shared agent context</summary>
        <div className="capacity-admin-content"><OrgContext /></div>
      </details>
    </div>
  );
}

function MachineOwner({
  machine,
  members,
  myId,
  isAdmin,
  onSetOwner,
}: {
  machine: MachineInfo;
  members: WorkspaceMember[];
  myId: string;
  isAdmin: boolean;
  onSetOwner: (machineId: string, ownerUserId: string) => void;
}) {
  const owner = machine.owner_user_id ?? "";
  const ownerLogin = members.find((m) => m.user.id === owner)?.user.github_login ?? owner;
  const title =
    "The machine's owner gets its auth todos (CLI logins, restart-me) — only they have hands on it.";
  if (isAdmin) {
    return (
      <select
        className="machine-owner"
        value={owner}
        title={title}
        onChange={(e) => onSetOwner(machine.id, e.target.value)}
      >
        <option value="">unclaimed</option>
        {members.map((m) => (
          <option key={m.user.id} value={m.user.id}>
            @{m.user.github_login}
          </option>
        ))}
      </select>
    );
  }
  if (!owner) {
    return (
      <button className="ghost" title={title} onClick={() => onSetOwner(machine.id, myId)}>
        claim as mine
      </button>
    );
  }
  if (owner === myId) {
    return (
      <span className="chip" title={title}>
        yours
        <button className="ghost quiet" onClick={() => onSetOwner(machine.id, "")}>
          release
        </button>
      </span>
    );
  }
  return (
    <span className="chip" title={title}>
      @{ownerLogin}
    </span>
  );
}

function MachineCard({
  card, activity, probing, updatingResource, runnerOnline, members, myId, isAdmin,
  onProbe, onSetEnabled, onForget, onSetOwner,
}: {
  card: MachineGroup;
  activity?: OverviewMachine;
  probing: string | null;
  updatingResource: string | null;
  runnerOnline: (id: string) => boolean;
  members: WorkspaceMember[];
  myId: string;
  isAdmin: boolean;
  onProbe: (id: string) => void;
  onSetEnabled: (res: ResourceInfo, enabled: boolean) => void;
  onForget: (id: string) => void;
  onSetOwner: (machineId: string, ownerUserId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const usable = card.resources.filter(resource => resource.available).length;
  const canManage = isAdmin || card.machine.owner_user_id === myId;
  const status = !card.online ? card.machine.asleep ? "Asleep" : "Offline"
    : activity?.runners_busy ? `${activity.runners_busy} running`
    : activity?.runners_idle ? "Ready" : "Online";
  const runnerNames = new Map(card.runners.map(runner => [runner.id, runner.name]));

  return (
    <article className="capacity-machine" aria-label={card.machine.name}>
      <header className="capacity-machine-head">
        <span className="capacity-device-icon" aria-hidden>{card.machine.device_kind === "server" ? <IconServer size={20} /> : <IconDeviceLaptop size={20} />}</span>
        <div className="capacity-machine-name"><h2>{card.machine.name}</h2>
          <p className="muted" title={machineTypeTitle(card.machine)}>{machineTypeLabel(card.machine)} · {deviceKindLabel(card.machine.device_kind)}</p>
        </div>
        <span className={`capacity-status ${card.online ? "online" : "offline"}`}><i aria-hidden />{status}</span>
        <div className="capacity-machine-count"><strong>{usable} usable {usable === 1 ? "agent" : "agents"}</strong>
          <span className="muted">{card.online ? "Heartbeat active" : card.last_seen > 0 ? `Last seen ${ago(card.last_seen)}` : "No heartbeat recorded"}</span>
        </div>
        <button type="button" className="ghost" aria-expanded={expanded}
          aria-controls={`agents-${card.machine.id}`} onClick={() => setExpanded(!expanded)}>
          {expanded ? "Hide details" : "Manage agents"}
        </button>
      </header>
      {!expanded && card.resources.length > 0 && <div className="capacity-agent-preview">
        {card.resources.map(resource => <span key={resource.id}><strong>{resource.backend}</strong>
          {availability(resource, runnerOnline(resource.runner_id))}</span>)}
      </div>}
      {expanded && <div className="capacity-machine-content" id={`agents-${card.machine.id}`}>
        <div className="capacity-machine-settings">
          <span className="muted" title={deviceKindTitle(card.machine.device_kind)}>
            {card.machine.hostname || card.machine.name}{card.machine.power_policy === "on_demand" ? " · power managed by Hive" : ""}
          </span>
          <div className="capacity-owner"><span>Owner</span>
            {!isUnlinkedMachine(card.machine) ? <MachineOwner machine={card.machine} members={members}
              myId={myId} isAdmin={isAdmin} onSetOwner={onSetOwner} /> : <span>Unlinked machine</span>}
          </div>
          {!card.online && canManage && !isUnlinkedMachine(card.machine) && <button type="button" className="ghost"
            onClick={() => { if (confirm(`Forget ${card.machine.name}? Its agents are removed. A running runner will re-register.`)) onForget(card.machine.id); }}>
            Forget machine
          </button>}
        </div>
        {card.resources.length === 0 && <p className="muted">No agent CLIs discovered on this machine.</p>}
        <div className="capacity-agent-list">
          {card.resources.map(resource => <article className="capacity-agent" key={resource.id}
            aria-label={`${resource.backend} on ${card.machine.name}`}>
            <header className="capacity-agent-head">
              <div><strong className="capacity-agent-name">{resource.backend}</strong>
                {card.runners.length > 1 && <span className="muted"> · {runnerNames.get(resource.runner_id)}</span>}
              </div>
              {availability(resource, runnerOnline(resource.runner_id))}
              {canManage && <div className="capacity-agent-actions">
                {isResourceEnabled(resource) ? <>
                  <button type="button" className="ghost" onClick={() => onProbe(resource.id)}
                    disabled={!runnerOnline(resource.runner_id) || resource.usability_status === "probing" || probing === resource.id}>
                    {probing === resource.id || resource.usability_status === "probing" ? "Checking…" : "Check access"}
                  </button>
                  <button type="button" className="ghost" onClick={() => onSetEnabled(resource, false)}
                    disabled={updatingResource === resource.id || resource.usability_status === "probing"}>Disable</button>
                </> : <button type="button" className="ghost" onClick={() => onSetEnabled(resource, true)}
                  disabled={updatingResource === resource.id}>Enable</button>}
              </div>}
            </header>
            <details className="capacity-diagnostics"><summary>Access &amp; diagnostics</summary>
              <dl><div><dt>Discovery</dt><dd>{resource.discovery_status}{resource.cli_version ? ` · ${resource.cli_version}` : ""}</dd></div>
                <div><dt>Access check</dt><dd>{resource.usability_status}{resource.last_probe_at > 0 ? ` · ${ago(resource.last_probe_at)}` : " · never checked"}</dd></div>
                <div><dt>Completed attempts</dt><dd>{resource.total_tasks}</dd></div>
                <div><dt>Recorded spend</dt><dd>{money(resource.total_cost_usd)}</dd></div>
              </dl>
              {resource.disabled_reason && <p>{resource.disabled_reason}</p>}
              <pre>{discoveryTitle(resource)}</pre><pre>{usabilityTitle(resource)}</pre>
              {resource.last_exhaustion_text && <pre>{exhaustionTitle(resource)}</pre>}
            </details>
          </article>)}
        </div>
        {activity && <p className="capacity-accounting muted">{activity.runners_busy} busy · {activity.runners_idle} spare usable runners on this machine.</p>}
      </div>}
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

function availability(res: ResourceInfo, online: boolean) {
  if (!isResourceEnabled(res)) {
    return <span className="avail wait" title={res.disabled_reason || "resource is disabled"}>Disabled</span>;
  }
  if (!online) return <span className="avail wait">Runner offline</span>;
  if (res.available) return <span className="avail ok" title="Access is usable. This runner may already be working, and project requirements still apply.">Usable</span>;
  if (res.cooldown_until > Date.now() / 1000) {
    const retryHint = res.last_exhaustion_text ? exhaustionRetryHint(res.last_exhaustion_text) : undefined;
    const label = res.last_exhaustion_text
      ? retryHint
        ? `Quota exhausted · ${retryHint}`
        : `Quota exhausted · ${countdown(res.cooldown_until)}`
      : `Cooldown ${countdown(res.cooldown_until)}`;
    return (
      <span className="avail cool" title={res.last_exhaustion_text ? exhaustionTitle(res) : undefined}>
        {label}
      </span>
    );
  }
  const status = res.usability_status === "probing" ? "Checking access"
    : res.usability_status === "failed" ? "Access failed"
    : res.usability_status === "unknown" ? "Check needed" : "Unavailable";
  return <span className="avail wait" title={usabilityTitle(res)}>{status}</span>;
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
