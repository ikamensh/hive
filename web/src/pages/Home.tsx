import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IconAlertCircle, IconArrowRight, IconCircleCheck, IconClock, IconCoin, IconDeviceDesktop, IconDeviceLaptop, IconGitCommit, IconPlayerPause, IconPlayerPlay, IconServer } from "@tabler/icons-react";
import { ago, api, duration, money } from "../api";
import { useOverview } from "../App";
import { ProviderName, UsageLimits } from "../features/capacity/UsageLimits";
import { NewWorkButton } from "../features/work/NewWork";
import type { Overview, OverviewLiveTask, OverviewMachine, RecentDelivery } from "../types";
import "./home.css";

const STAGES: Record<OverviewLiveTask["kind"], string> = {
  work: "Building", verify: "Reviewing", probe: "Checking access", intake: "Designing", resolve: "Building",
  review: "Reviewing", preflight: "Checking readiness", test_refresh: "Preparing tests", test_sweep: "Testing",
  test_reproduce: "Reproducing", test_judge: "Checking finding", testability_draft: "Designing tests", testability_probe: "Checking tests",
};

function statusOf(data: Overview, stale: boolean) {
  if (stale) return { title: "Hive status unavailable", note: "The last observation is out of date. Reconnecting to the chief.", tone: "unknown" };
  const disconnected = data.execution.disconnected > 0 ? `${data.execution.disconnected} assigned ${data.execution.disconnected === 1 ? "task has" : "tasks have"} lost connection. ` : "";
  if (data.paused) {
    const connected = data.execution.working + data.execution.starting;
    const drain = connected > 0 ? `${connected} connected ${connected === 1 ? "task is" : "tasks are"} finishing. ` : "";
    return { title: "Hive is paused", note: `${drain}${disconnected}Nothing new will start until you resume.`, tone: "paused" };
  }
  if (data.execution.working > 0) return { title: "Hive is working", note: `${data.execution.working} ${data.execution.working === 1 ? "task is" : "tasks are"} running on connected machines. ${disconnected}`, tone: "working" };
  if (data.execution.starting > 0) return { title: "Hive is starting work", note: `${data.execution.starting} ${data.execution.starting === 1 ? "task is" : "tasks are"} assigned and waiting for the runner to pick up. ${disconnected}`, tone: "starting" };
  if (data.totals.tasks_running > 0) return { title: "Hive needs attention", note: "Assigned work has lost its runner connection. Check the machines below.", tone: "waiting" };
  const waiting = data.tasks_pending > 0 || data.projects.some(p => ["working", "needs_attention", "blocked_resources", "blocked_budget"].includes(p.state));
  if (waiting) return { title: "Hive is waiting", note: data.tasks_pending > 0
    ? `${data.tasks_pending} ${data.tasks_pending === 1 ? "task is" : "tasks are"} queued. Nothing is running yet.`
    : "Nothing is running. Review project state, capacity, or decisions to continue.", tone: "waiting" };
  return { title: "Hive is idle", note: data.projects.length > 0 ? "No work is running or queued. Start the next useful change." : "Connect a project and give Hive its first goal or plan.", tone: "idle" };
}

function taskLink(task: OverviewLiveTask) {
  if (!task.project_id) return `/machines${task.machine_id ? `#${task.machine_id}` : ""}`;
  const params = new URLSearchParams();
  if (task.plan_id) params.set("plan", task.plan_id);
  if (task.work_item_id) params.set("item", task.work_item_id);
  if (!task.plan_id) params.set("tab", "activity");
  return `/p/${task.project_id}?${params}`;
}

function MachineRow({ machine, task, stale }: { machine?: OverviewMachine; task?: OverviewLiveTask; stale: boolean }) {
  const DeviceIcon = machine?.device_kind === "laptop" ? IconDeviceLaptop : IconServer;
  const online = !stale && (task ? task.runner_online : machine?.online);
  const name = machine?.name || task?.machine_name || task?.runner_name || "Unassigned runner";
  const status = stale ? "Unknown" : !online ? task ? "Heartbeat lost" : "Offline" : task ? task.delivered ? "Online · Busy" : "Online · Starting" : machine && machine.runners_busy > 0 ? "Online · Busy" : machine && machine.runners_idle > 0 ? "Online · Idle" : "Online · Unavailable";
  const kind = stale ? "unknown" : !online ? task ? "waiting" : "offline" : "online";
  return <tr>
    <td className="op-machine-cell"><DeviceIcon size={23} stroke={1.5} aria-hidden /><div><Link to={`/machines${machine ? `#${machine.id}` : ""}`}>{name}</Link>{task && machine && machine.runners_total > 1 && <small>{task.runner_name}</small>}</div></td>
    <td><span className={`op-machine-status status-${kind}`}><span className="status-dot" />{status}</span>{!online && !stale && (task?.runner_last_seen || machine?.last_seen) ? <small className="op-cell-note">Last seen {ago(task?.runner_last_seen || machine!.last_seen)}</small> : null}</td>
    <td>{task ? <ProviderName backend={task.backend} /> : <span className="op-empty-label">{online && machine && machine.runners_idle > 0 ? "Ready for work" : "—"}</span>}</td>
    <td className="op-work-cell">{task ? <Link to={taskLink(task)}><span>{task.project_name}</span><strong>{task.title}</strong></Link> : <span className="op-empty-label">{!online ? "No connected runner" : machine && machine.runners_busy > 0 ? `${machine.runners_busy} active — open project details` : machine && machine.runners_idle > 0 ? "No active task" : "Check agent access or limits"}</span>}</td>
    <td>{task ? <span className={`op-stage ${!online ? "op-stage-waiting" : ""}`}>{stale ? "Last known: " : ""}{!online && !stale ? "Connection lost" : !task.delivered ? "Starting" : STAGES[task.kind]}</span> : <Link className="op-row-action" to="/machines">View capacity</Link>}</td>
    <td className="op-duration">{task ? duration(task.started_at, 0) : "—"}</td>
  </tr>;
}

function Delivery({ item }: { item: RecentDelivery }) {
  const labels = { passed: "Checks passed", failed: "Check failed", not_recorded: "Review evidence", different_commit: "Review evidence" };
  const VerdictIcon = item.validation.status === "passed" ? IconCircleCheck : item.validation.status === "failed" ? IconAlertCircle : IconGitCommit;
  return <Link className="op-delivery" to={`/p/${item.project_id}?plan=${encodeURIComponent(item.plan_id)}&tab=work&item=${encodeURIComponent(item.work_item_id)}&evidence=1`}>
    <div className="op-delivery-text"><strong>{item.project_name}</strong><span>{item.title}</span></div>
    <span className="op-delivery-age">{ago(item.merged_at)}</span>
    <span className={`op-delivery-verdict ${item.validation.status === "passed" ? "is-passed" : ""}`}><VerdictIcon size={15} aria-hidden />{labels[item.validation.status]}</span>
  </Link>;
}

export default function Home() {
  const { data, failed, refresh, inboxCount } = useOverview();
  const [pauseBusy, setPauseBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now() / 1000), 10000); return () => clearInterval(timer); }, []);
  if (!data) return <div className="page page-operations"><h1>Overview</h1><p className="empty-copy">{failed ? "Waiting for the chief connection." : "Checking Hive’s current state…"}</p></div>;
  const stale = failed || now - data.observed_at > 30;
  const status = statusOf(data, stale);
  const StatusIcon = status.tone === "working" ? IconCircleCheck : status.tone === "paused" ? IconPlayerPause : status.tone === "waiting" || status.tone === "unknown" ? IconAlertCircle : IconClock;
  const machines = [...data.capacity.machines].sort((a, b) => b.runners_busy - a.runners_busy || Number(b.online) - Number(a.online) || a.name.localeCompare(b.name));
  const visibleMachines = machines.slice(0, 6);
  const attachedTasks = new Set<string>();
  const machineRows = visibleMachines.flatMap(machine => {
    const live = data.live_tasks.filter(task => task.machine_id === machine.id || (!task.machine_id && machine.agents.some(agent => agent.runner_id === task.runner_id)));
    live.forEach(task => attachedTasks.add(task.id));
    return live.length ? live.map(task => <MachineRow key={task.id} machine={machine} task={task} stale={stale} />) : [<MachineRow key={machine.id} machine={machine} stale={stale} />];
  });
  const unassigned = data.live_tasks.filter(task => !attachedTasks.has(task.id) && !machines.some(machine => machine.id === task.machine_id));
  const readings = machines.flatMap(machine => machine.agents.map(agent => ({ ...agent, machineName: machine.name })));
  const pause = async () => {
    setPauseBusy(true); setError("");
    try { await api.setFleetPaused(!data.paused); await refresh(); }
    catch (err) { setError(String(err)); }
    finally { setPauseBusy(false); }
  };
  return <div className="page page-operations">
    <h1>Overview</h1>
    <header className={`op-status-header status-${status.tone}`}>
      <div className="op-state"><StatusIcon className="op-state-icon" size={41} stroke={1.7} aria-hidden /><div><h2>{status.title}</h2><p>{status.note}</p><small>Updated {ago(data.observed_at)}</small></div></div>
      <div className="op-header-actions"><NewWorkButton onStarted={refresh} /><button className="ghost" disabled={pauseBusy || stale} onClick={pause}>{data.paused ? <IconPlayerPlay size={18} aria-hidden /> : <IconPlayerPause size={18} aria-hidden />}{pauseBusy ? "Updating…" : data.paused ? "Resume Hive" : "Pause Hive"}</button></div>
    </header>
    {error && <p className="form-error" role="alert">{error}</p>}
    <section className="op-summary" aria-label="Current capacity">
      <Link to="/machines"><IconDeviceDesktop size={24} stroke={1.6} aria-hidden /><span><strong>{stale ? "—" : `${data.capacity.machines_online} of ${data.capacity.machines_total}`}</strong> machines online</span></Link>
      <div><IconPlayerPlay size={23} stroke={1.6} aria-hidden /><span><strong>{stale ? "—" : data.capacity.runners_busy}</strong> busy</span></div>
      <Link to="/machines"><IconDeviceDesktop size={24} stroke={1.6} aria-hidden /><span><strong>{stale ? "—" : data.capacity.runners_idle}</strong> idle &amp; usable</span></Link>
      <div title="Recorded task and planner spend. In-flight costs may not yet be reported."><IconCoin size={24} stroke={1.6} aria-hidden /><span>Recorded spend <strong>{money(data.totals.spend_today)}</strong> today</span></div>
    </section>
    <section className="op-machine-section">
      <div className="op-section-head"><h2>Where work is running</h2><Link to="/machines">View capacity</Link></div>
      <p className="op-section-note">One task at a time per runner. Agent CLIs on the same machine share that capacity.</p>
      {machineRows.length === 0 && unassigned.length === 0 ? <div className="op-empty"><IconServer size={28} stroke={1.5} aria-hidden /><div><strong>No machines connected</strong><p>Enroll a machine to give Hive somewhere to run work.</p><Link to="/machines">Connect a machine <IconArrowRight size={15} aria-hidden /></Link></div></div> :
      <div className="op-table-wrap"><table className="op-machine-table"><thead><tr><th>Machine</th><th>Status</th><th>Agent</th><th>Project / work</th><th>Stage</th><th>Elapsed</th></tr></thead><tbody>{machineRows}{unassigned.map(task => <MachineRow key={task.id} task={task} stale={stale} />)}</tbody></table></div>}
      {machines.length > visibleMachines.length && <Link className="op-more" to="/machines">View all {machines.length} machines</Link>}
    </section>
    <div className="op-support-grid">
      <section className="op-results"><div className="op-section-head"><h2>Recent results</h2><Link to="/results">View all</Link></div>
        {data.recent_deliveries.items.length === 0 ? <p className="empty-copy">Landed changes and their evidence will appear here.</p> : data.recent_deliveries.items.slice(0, 3).map(item => <Delivery key={item.work_item_id} item={item} />)}
        {data.recent_deliveries.count > 3 && <p className="op-section-note">Latest 3 of {data.recent_deliveries.count} delivered changes</p>}
      </section>
      <section className="op-limits"><div className="op-section-head"><h2>Subscription limits</h2><Link to="/machines?tab=limits">View all</Link></div><UsageLimits readings={readings} compact /></section>
    </div>
    {inboxCount !== null && inboxCount > 0 && <Link className="op-attention" to="/needs-you"><IconAlertCircle size={21} aria-hidden /><span><strong>{inboxCount} {inboxCount === 1 ? "action" : "actions"} waiting</strong><span>Open Inbox</span></span><IconArrowRight size={18} aria-hidden /></Link>}
    {import.meta.env.VITE_MOCK === "1" && <p className="op-demo-note">Sample data · Local design preview</p>}
  </div>;
}
