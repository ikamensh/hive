import { Fragment, useState } from "react";
import { api, repoShort } from "../../api";
import { Markdown } from "../../components/shared";
import type {
  CiCheckResult,
  PreflightCheck,
  PreflightResult,
  IssueRun,
  Project,
  ScanResult,
  IssueItem,
  IssueItemStatus,
  ProjectWorkstream,
} from "../../types";
import { CheckList, checksFromError, PreflightSummary } from "./preflight";

const ACTIVE_ISSUE_RUN_STATUSES = new Set<IssueRun["status"]>(["scanning", "queued", "running", "blocked"]);

/** Derive the issue's branch tree URL from its issue URL (`.../issues/42` to `.../tree/hive/issue-42`). */
function issueBranchUrl(ws: IssueItem): string | null {
  if (!ws.issue_url || ws.issue_number === undefined) return null;
  return ws.issue_url.replace(/\/issues\/\d+.*$/, `/tree/hive/issue-${ws.issue_number}`);
}

export function IssuesToolbar({
  project,
  issueStreams,
  selectedStreamId,
  onSelectedStream,
  selectedNumbers,
  issueRuns,
  onChanged,
}: {
  project: Project;
  issueStreams: ProjectWorkstream[];
  selectedStreamId: string;
  onSelectedStream: (id: string) => void;
  selectedNumbers: number[];
  issueRuns: IssueRun[];
  onChanged: () => void;
}) {
  const [busyAction, setBusyAction] = useState<"preflight" | "sync" | "run" | "ci" | "">("");
  const [cancellingRunId, setCancellingRunId] = useState("");
  const [result, setResult] = useState<ScanResult | null>(null);
  const [ci, setCi] = useState<CiCheckResult | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [error, setError] = useState("");
  const [errorChecks, setErrorChecks] = useState<PreflightCheck[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [scope, setScope] = useState<"selected" | "all_open_now" | "scan_only">("selected");
  const stream = issueStreams.find((w) => w.id === selectedStreamId) ?? issueStreams[0];
  const activeRun = [...issueRuns]
    .filter((run) => run.workstream_id === stream?.id && ACTIVE_ISSUE_RUN_STATUSES.has(run.status))
    .sort((a, b) => b.created_at - a.created_at)[0];
  const noRepo = !stream;
  const streamDisabled = Boolean(stream && (!stream.enabled || stream.status === "disabled"));
  const busy = busyAction !== "" || cancellingRunId !== "";

  const runPreflight = async () => {
    if (!stream) return;
    setBusyAction("preflight");
    setError("");
    setErrorChecks([]);
    try {
      setPreflight(await api.workstreamPreflight(project.id, stream.id));
      onChanged();
    } catch (e) {
      setError((e as Error).message || "preflight failed");
      setErrorChecks(checksFromError(e));
    }
    setBusyAction("");
  };

  const sync = async () => {
    if (!stream) return;
    setBusyAction("sync");
    setError("");
    setErrorChecks([]);
    try {
      setResult(await api.syncIssues(project.id, stream.id));
      onChanged();
    } catch (e) {
      setError((e as Error).message || "sync failed");
      setErrorChecks(checksFromError(e));
    }
    setBusyAction("");
  };

  const checkCi = async () => {
    if (!stream) return;
    setBusyAction("ci");
    setError("");
    setErrorChecks([]);
    setCi(null);
    try {
      setCi(await api.checkCi(project.id, stream.id));
      onChanged();
    } catch (e) {
      setError((e as Error).message || "CI check failed");
      setErrorChecks(checksFromError(e));
    }
    setBusyAction("");
  };

  const run = async () => {
    if (!stream) return;
    setBusyAction("run");
    setError("");
    setErrorChecks([]);
    try {
      const response = await api.runIssues(project.id, stream.id, {
        scope,
        issue_numbers: scope === "selected" ? selectedNumbers : [],
      });
      setResult(response);
      setDrawerOpen(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message || "run failed");
      setErrorChecks(checksFromError(e));
    }
    setBusyAction("");
  };

  const cancelRun = async () => {
    if (!activeRun) return;
    setCancellingRunId(activeRun.id);
    setError("");
    setErrorChecks([]);
    try {
      await api.cancelIssueRun(activeRun.id);
      setDrawerOpen(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message || "cancel failed");
      setErrorChecks(checksFromError(e));
    }
    setCancellingRunId("");
  };

  return (
    <section className="scan-bar reveal">
      <div className="scan-text">
        <h2>Issues</h2>
        <select
          value={stream?.id ?? ""}
          onChange={(event) => onSelectedStream(event.target.value)}
          disabled={issueStreams.length <= 1}
        >
          {issueStreams.length === 0 && <option value="">no GitHub issue workstream</option>}
          {issueStreams.map((w) => (
            <option value={w.id} key={w.id}>{repoShort(w.repo)}</option>
          ))}
        </select>
      </div>
      <div className="scan-actions">
        {streamDisabled && <span className="muted">disabled in settings</span>}
        <div className="scan-buttons">
          <button className="ghost" onClick={runPreflight} disabled={busy || noRepo || streamDisabled} title={noRepo ? "set a spec repo first" : undefined}>
            {busyAction === "preflight" ? "checking..." : "preflight"}
          </button>
          <button className="ghost" onClick={sync} disabled={busy || noRepo || streamDisabled} title={noRepo ? "set a GitHub repo first" : undefined}>
            {busyAction === "sync" ? "syncing..." : "sync"}
          </button>
          <button
            className="ghost"
            onClick={checkCi}
            disabled={busy || noRepo || streamDisabled}
            title={noRepo ? "set a GitHub repo first" : "check this repo's CI and auto-fix a red build"}
          >
            {busyAction === "ci" ? "checking CI..." : "check CI"}
          </button>
          <button
            onClick={() => setDrawerOpen((v) => !v)}
            disabled={busy || noRepo || streamDisabled || Boolean(activeRun)}
            title={
              noRepo
                ? "set a GitHub repo first"
                : activeRun
                  ? "cancel the active issue run before starting another"
                  : undefined
            }
          >
            {activeRun ? "run active" : "run issues"}
          </button>
        </div>
        {activeRun && (
          <div className="issue-run-status">
            <span>
              run {activeRun.status.replace(/_/g, " ")} - {activeRun.counts.running ?? 0} running, {activeRun.counts.queued ?? 0} queued
            </span>
            <button className="danger-outline" onClick={cancelRun} disabled={busy}>
              {cancellingRunId === activeRun.id ? "cancelling..." : "cancel run"}
            </button>
          </div>
        )}
        {drawerOpen && !activeRun && (
          <div className="issue-run-drawer">
            <label>
              <input
                type="radio"
                checked={scope === "selected"}
                onChange={() => setScope("selected")}
              />
              selected issues ({selectedNumbers.length})
            </label>
            <label>
              <input
                type="radio"
                checked={scope === "all_open_now"}
                onChange={() => setScope("all_open_now")}
              />
              all currently open
            </label>
            <label>
              <input
                type="radio"
                checked={scope === "scan_only"}
                onChange={() => setScope("scan_only")}
              />
              scan only
            </label>
            <button onClick={run} disabled={busy || (scope === "selected" && selectedNumbers.length === 0)}>
              {busyAction === "run" ? "starting..." : "start run"}
            </button>
          </div>
        )}
        {error && (
          <div className="scan-error">
            <span className="form-error">{error}</span>
            <CheckList checks={errorChecks} />
          </div>
        )}
        {ci && !error && (
          <span className="scan-summary">
            CI {ci.conclusion} on {ci.branch || "default"}
            {ci.conclusion === "failing" ? (
              ci.already_filed ? (
                <> - already tracking <a href={ci.filed_issue_url} target="_blank" rel="noreferrer">#{ci.filed_issue}</a></>
              ) : ci.filed_issue ? (
                <>
                  {" "}- filed{" "}
                  <a href={ci.filed_issue_url} target="_blank" rel="noreferrer">#{ci.filed_issue}</a>
                  {ci.resolve_queued > 0 ? " - fix queued" : ""}
                </>
              ) : null
            ) : (
              <> - nothing to fix</>
            )}
          </span>
        )}
        {preflight && !error && <PreflightSummary result={preflight} />}
        {result && !error && (
          <span className="scan-summary">
            last update: {result.open_issues} open - {result.resolve_queued} queued
            {(result.attachments_downloaded > 0 || result.attachments_failed > 0) && (
              <> - attachments {result.attachments_downloaded} ok / {result.attachments_failed} failed</>
            )}
            {result.changes.length > 0 && (
              <ul>
                {result.changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}
          </span>
        )}
      </div>
    </section>
  );
}

export function IssueCard({ ws }: { ws: IssueItem }) {
  const [open, setOpen] = useState(false);
  const branch = issueBranchUrl(ws);
  return (
    <article className={`issue-card iss-${ws.status}`}>
      <header>
        <button className="issue-title" onClick={() => setOpen((v) => !v)}>
          {ws.title}
        </button>
        <span className={`chip chip-iss-${ws.status}`}>{ws.status.replace(/_/g, " ")}</span>
      </header>
      {ws.parked_reason && <p className="parked-reason">{ws.parked_reason}</p>}
      <div className="issue-links">
        {ws.issue_url && (
          <a href={ws.issue_url} target="_blank" rel="noreferrer">
            issue #{ws.issue_number}
          </a>
        )}
        {branch && (
          <a href={branch} target="_blank" rel="noreferrer">
            branch hive/issue-{ws.issue_number}
          </a>
        )}
        {ws.description && (
          <button className="issue-detail-toggle" onClick={() => setOpen((v) => !v)}>
            {open ? "hide details" : "details"}
          </button>
        )}
      </div>
      {open && ws.description && <Markdown className="issue-detail" text={ws.description} />}
    </article>
  );
}

const ISSUE_GROUPS: { label: string; statuses: IssueItemStatus[] }[] = [
  { label: "ready", statuses: ["queued"] },
  { label: "running", statuses: ["resolving", "reviewing"] },
  { label: "needs you", statuses: ["blocked_clarity", "rejected"] },
  { label: "done", statuses: ["done", "cancelled"] },
];

function issueSort(group: { statuses: IssueItemStatus[] }) {
  return (a: IssueItem, b: IssueItem) => {
    if (group.statuses.includes("queued")) {
      return (a.order ?? Number.MAX_SAFE_INTEGER) - (b.order ?? Number.MAX_SAFE_INTEGER) ||
        (a.issue_number ?? 0) - (b.issue_number ?? 0) ||
        a.created_at - b.created_at;
    }
    return b.created_at - a.created_at;
  };
}

export function IssuesView({
  workItems,
  selectedNumbers,
  onToggle,
}: {
  workItems: IssueItem[];
  selectedNumbers: number[];
  onToggle: (issueNumber: number) => void;
}) {
  const [filter, setFilter] = useState("ready");
  const [openIssue, setOpenIssue] = useState<number | null>(null);
  const issues = workItems.filter((w) => w.issue_number);
  const group = ISSUE_GROUPS.find((g) => g.label === filter) ?? ISSUE_GROUPS[0];
  const items = issues
    .filter((w) => group.statuses.includes(w.status))
    .sort(issueSort(group));
  return (
    <section className="issues-view">
      {issues.length === 0 && <p className="muted">no issues yet - sync to ingest open GitHub issues</p>}
      {issues.length > 0 && (
        <>
          <div className="issue-filter">
            {ISSUE_GROUPS.map((g) => {
              const count = issues.filter((w) => g.statuses.includes(w.status)).length;
              return (
                <button
                  className={filter === g.label ? "active" : "ghost"}
                  key={g.label}
                  onClick={() => setFilter(g.label)}
                >
                  {g.label} <span className="col-count">{count}</span>
                </button>
              );
            })}
          </div>
          <div className="issue-table-wrap">
            <table className="issue-table">
              <thead>
                <tr>
                  <th aria-label="select" />
                  <th>issue</th>
                  <th>state</th>
                  <th>repo</th>
                  <th>note</th>
                  <th>branch</th>
                </tr>
              </thead>
              <tbody>
                {items.map((w) => {
                  const checked = selectedNumbers.includes(w.issue_number ?? 0);
                  const branch = issueBranchUrl(w);
                  const open = openIssue === w.issue_number;
                  return (
                    <Fragment key={w.id}>
                      <tr>
                        <td>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => w.issue_number && onToggle(w.issue_number)}
                          />
                        </td>
                        <td>
                          <button className="issue-title" onClick={() => setOpenIssue(open ? null : w.issue_number ?? null)}>
                            {w.title}
                          </button>
                        </td>
                        <td><span className={`chip chip-iss-${w.status}`}>{w.status.replace(/_/g, " ")}</span></td>
                        <td>{repoShort(w.repo || "")}</td>
                        <td>{w.parked_reason || "-"}</td>
                        <td>
                          {branch ? (
                            <a href={branch} target="_blank" rel="noreferrer">branch</a>
                          ) : (
                            <span className="muted">-</span>
                          )}
                        </td>
                      </tr>
                      {open && w.description && (
                        <tr className="issue-detail-row">
                          <td />
                          <td colSpan={5}>
                            <Markdown className="issue-detail" text={w.description} />
                            {w.issue_url && <a href={w.issue_url} target="_blank" rel="noreferrer">open on GitHub</a>}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
