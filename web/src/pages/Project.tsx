import { useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, ago, api, duration, money, repoShort, usePoll } from "../api";
import { RepoListEditor, RepoUrlInput } from "../components/RepoPicker";
import {
  AUTONOMY_OPTIONS,
  GuessSlider,
  Markdown,
  MODE_OPTIONS,
  SegPicker,
  StateBadge,
  WORK_SOURCE_OPTIONS,
} from "../components/shared";
import type { AgentConversation, Autonomy, GuessPropensity, HumanTask, Mode, PreflightCheck, PreflightResult, Project, ProjectPatch, Question, ResourceInfo, ScanResult, Task, WorkSource, Workstream, WorkstreamStatus } from "../types";

/** Derive the issue's per-issue branch tree URL from its issue URL (`.../issues/42` → `.../tree/hive/issue-42`). */
function issueBranchUrl(ws: Workstream): string | null {
  if (!ws.issue_url || ws.issue_number === undefined) return null;
  return ws.issue_url.replace(/\/issues\/\d+.*$/, `/tree/hive/issue-${ws.issue_number}`);
}

function buildSetupPatch(fields: {
  specRepo: string;
  memberRepos: string[];
  mode: Mode;
  autonomy: Autonomy;
  workSource: WorkSource;
  guess: GuessPropensity;
  dailyBudget: string;
}): ProjectPatch {
  const budget = parseFloat(fields.dailyBudget);
  return {
    spec_repo: fields.specRepo.trim(),
    member_repos: fields.memberRepos.map((s) => s.trim()).filter(Boolean),
    mode: fields.mode,
    autonomy: fields.autonomy,
    work_source: fields.workSource,
    guess_propensity: fields.guess,
    daily_budget_usd: Number.isFinite(budget) && budget >= 0 ? budget : 0,
  };
}

function scoutStateLabel(resource: ResourceInfo): string {
  if (resource.available) return "ready";
  if (resource.enabled === false) return "disabled";
  if (resource.cooldown_until > Date.now() / 1000) return "cooldown";
  return resource.usability_status === "usable" ? "unavailable" : resource.usability_status;
}

function intakeSection(text: string, heading: string): string {
  const escaped = heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = text.match(
    new RegExp(`(?:^|\\n)(?:#+\\s*)?${escaped}\\s*:?\\s*\\n([\\s\\S]*?)(?=\\n(?:#+\\s*)?[A-Za-z][A-Za-z ]{1,40}\\s*:?\\s*\\n|$)`, "i"),
  );
  return match?.[1]?.trim() ?? "";
}

function intakeBriefReady(text: string): boolean {
  const required = ["Mission", "Next iteration", "Likely next steps", "Assumptions", "Questions"];
  if (required.some((heading) => !intakeSection(text, heading))) return false;
  const normalized = intakeSection(text, "Questions")
    .replace(/^[\s>*#`\-•0-9.)]+/gm, "")
    .toLowerCase()
    .replace(/[^a-z]+/g, " ")
    .trim();
  return new Set([
    "",
    "none",
    "n a",
    "no questions",
    "no material questions",
    "no remaining questions",
    "no remaining material questions",
  ]).has(normalized);
}

function ProjectSetup({
  project,
  conversation,
  trustedScouts,
  onSave,
  onCreateRepo,
  onStartIntake,
  onConversationMessage,
}: {
  project: Project;
  conversation: AgentConversation | null;
  trustedScouts: ResourceInfo[];
  onSave: (patch: ProjectPatch) => Promise<void>;
  onCreateRepo: (repoName: string) => Promise<void>;
  onStartIntake: (patch: ProjectPatch) => Promise<void>;
  onConversationMessage: (conversationId: string, action: "message" | "proceed" | "approve", message?: string) => Promise<void>;
}) {
  const [specRepo, setSpecRepo] = useState(project.spec_repo);
  const [memberRepos, setMemberRepos] = useState(project.member_repos);
  const [repoName, setRepoName] = useState(project.name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-|-$/g, ""));
  const [intakeMessage, setIntakeMessage] = useState("");
  const [mode, setMode] = useState<Mode>(project.mode);
  const [autonomy, setAutonomy] = useState<Autonomy>(project.autonomy);
  const [workSource, setWorkSource] = useState<WorkSource>(project.work_source);
  const [guess, setGuess] = useState<GuessPropensity>(project.guess_propensity);
  const [dailyBudget, setDailyBudget] = useState(
    project.daily_budget_usd > 0 ? String(project.daily_budget_usd) : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const fields = { specRepo, memberRepos, mode, autonomy, workSource, guess, dailyBudget };
  const draft = !project.spec_repo.trim();
  const issuesSetup = workSource === "issues";
  const intakeRunning = conversation?.status === "running" || conversation?.status === "finalizing";
  const intakeDone = conversation?.status === "done";
  const intakeReady = intakeBriefReady(conversation?.latest_brief ?? "");

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await onSave(buildSetupPatch(fields));
    } catch (e) {
      setError((e as Error).message || "save failed");
    }
    setBusy(false);
  };

  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!specRepo.trim()) {
      setError("spec repo URL is required");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (issuesSetup) {
        await onSave(buildSetupPatch(fields));
      } else {
        await onStartIntake(buildSetupPatch(fields));
      }
    } catch (e) {
      setError((e as Error).message || (issuesSetup ? "save failed" : "intake failed to start"));
    }
    setBusy(false);
  };

  const createRepo = async () => {
    setBusy(true);
    setError("");
    try {
      await onCreateRepo(repoName.trim());
    } catch (e) {
      setError((e as Error).message || "repo creation failed");
    }
    setBusy(false);
  };

  const sendConversation = async (action: "message" | "proceed" | "approve") => {
    if (!conversation) return;
    setBusy(true);
    setError("");
    try {
      await onConversationMessage(conversation.id, action, intakeMessage.trim());
      if (action === "message") setIntakeMessage("");
    } catch (e) {
      setError((e as Error).message || "could not send intake message");
    }
    setBusy(false);
  };

  return (
    <section className="setup-panel reveal">
      <header className="setup-head">
        <h2>{issuesSetup ? "Configure issues mode" : intakeDone ? "Intake complete" : draft ? "Configure intake" : "Project intake"}</h2>
        <p className="muted">
          {issuesSetup
            ? "Choose the GitHub repo whose issues Hive should resolve, then save and scan."
            : draft
            ? "Choose a repo, or create one for this project, then start the scout."
            : "The scout aligns mission, next iteration, and assumptions before planning starts."}
        </p>
      </header>
      <form className="setup-form" onSubmit={start}>
        <label>
          spec repo
          <RepoUrlInput
            value={specRepo}
            onChange={setSpecRepo}
            placeholder="git@github.com:org/project-spec.git"
          />
        </label>
        <label>
          member repos
          <RepoListEditor repos={memberRepos} onChange={setMemberRepos} />
        </label>
        {draft && (
          <div className="create-repo-row">
            <label>
              new private repo
              <input value={repoName} onChange={(e) => setRepoName(e.target.value)} placeholder="repo-name" />
            </label>
            <button type="button" className="ghost" onClick={createRepo} disabled={busy || !repoName.trim()}>
              create repo
            </button>
          </div>
        )}
        <div className="dial-grid">
          <label>
            work source
            <SegPicker value={workSource} options={WORK_SOURCE_OPTIONS} onChange={setWorkSource} />
          </label>
          <label>
            mode
            <SegPicker value={mode} options={MODE_OPTIONS} onChange={setMode} />
          </label>
          <label>
            autonomy
            <SegPicker value={autonomy} options={AUTONOMY_OPTIONS} onChange={setAutonomy} />
          </label>
        </div>
        {!issuesSetup && (
          <label>
            guess propensity
            <GuessSlider value={guess} onChange={setGuess} />
          </label>
        )}
        <label>
          daily budget (USD, 0 = no cap)
          <input
            type="number"
            min={0}
            step={1}
            value={dailyBudget}
            onChange={(e) => setDailyBudget(e.target.value)}
            placeholder="0"
          />
        </label>
        {!issuesSetup && (
          <div className="trusted-scouts">
            <span className="field-label">trusted scouts</span>
            <div>
              {trustedScouts.length === 0 && <span className="chip chip-failed">unavailable</span>}
              {trustedScouts.map((resource) => (
                <span
                  className={`chip ${resource.available ? "chip-open" : "chip-failed"}`}
                  key={resource.id}
                  title={resource.disabled_reason || resource.last_exhaustion_text || resource.last_probe_text}
                >
                  {resource.backend === "codex" ? "codex" : "claude"} · {scoutStateLabel(resource)}
                </span>
              ))}
            </div>
          </div>
        )}
        {error && <p className="form-error">{error}</p>}
        {conversation && (
          <div className={`intake-brief intake-${conversation.status}`}>
            <header>
              <span className={`chip chip-${conversation.status}`}>{conversation.status}</span>
              <span className="muted">{conversation.backend} {conversation.model}</span>
            </header>
            {conversation.latest_brief ? <Markdown text={conversation.latest_brief} /> : <p className="muted">waiting for the scout brief</p>}
            {!intakeDone && (
              <div className="intake-actions">
                <textarea
                  value={intakeMessage}
                  onChange={(e) => setIntakeMessage(e.target.value)}
                  rows={4}
                  placeholder="Answer or correct the scout..."
                  disabled={busy || intakeRunning}
                />
                <div className="setup-actions">
                  <button type="button" className="ghost" onClick={() => sendConversation("message")} disabled={busy || intakeRunning || !intakeMessage.trim()}>
                    send answer
                  </button>
                  <button type="button" className="ghost" onClick={() => sendConversation("proceed")} disabled={busy || intakeRunning}>
                    proceed with assumptions
                  </button>
                  <button type="button" onClick={() => sendConversation("approve")} disabled={busy || intakeRunning || !intakeReady}>
                    approve and finalize
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
        <div className="setup-actions">
          <button type="button" className="ghost" onClick={save} disabled={busy}>
            {busy ? "saving…" : "save"}
          </button>
          <button
            type="submit"
            disabled={busy || !specRepo.trim() || (!issuesSetup && (intakeRunning || intakeDone || Boolean(conversation)))}
          >
            {busy ? (issuesSetup ? "saving…" : "starting…") : issuesSetup ? "save issues project" : conversation ? "intake started" : "start intake"}
          </button>
        </div>
      </form>
    </section>
  );
}

function ProjectSettings({
  project,
  onPatch,
}: {
  project: Project;
  onPatch: (p: ProjectPatch) => void;
}) {
  const [memberRepos, setMemberRepos] = useState(project.member_repos);
  const [dailyBudget, setDailyBudget] = useState(
    project.daily_budget_usd > 0 ? String(project.daily_budget_usd) : "",
  );
  const [busy, setBusy] = useState(false);

  const save = async (e: React.FormEvent) => {
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
          {busy ? "saving…" : "save settings"}
        </button>
      </form>
    </details>
  );
}

function TogglesBar({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
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
      <div className="toggle-cell">
        <span className="toggle-label">work source</span>
        <SegPicker
          value={project.work_source}
          options={WORK_SOURCE_OPTIONS}
          onChange={(work_source) => onPatch({ work_source })}
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

function GoalBanner({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
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

function WorkstreamCard({ ws }: { ws: Workstream }) {
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

function checksFromError(error: unknown): PreflightCheck[] {
  const detail = error instanceof ApiError ? error.detail : undefined;
  if (!detail || typeof detail !== "object") return [];
  const checks = (detail as { checks?: unknown }).checks;
  if (!Array.isArray(checks)) return [];
  return checks.filter((check): check is PreflightCheck => {
    if (!check || typeof check !== "object") return false;
    const c = check as Partial<PreflightCheck>;
    return typeof c.name === "string" && typeof c.ok === "boolean" && typeof c.detail === "string";
  });
}

function CheckList({ checks }: { checks: PreflightCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <ul className="scan-checks">
      {checks.map((check) => (
        <li key={check.name} className={check.ok ? "ok" : check.hard ? "fail" : "warn"}>
          <span>{check.ok ? "pass" : check.hard ? "fail" : "warn"}</span>
          <b>{check.name.replace(/_/g, " ")}</b>
          <small>{check.detail}</small>
        </li>
      ))}
    </ul>
  );
}

function PreflightSummary({ result }: { result: PreflightResult }) {
  return (
    <div className={`preflight-summary ${result.ok ? "ok" : "blocked"}`}>
      <span>{result.ok ? "preflight passed" : "preflight blocked"}</span>
      {result.runner_check_task && <small>runner check queued in activity</small>}
      <CheckList checks={result.checks} />
    </div>
  );
}

function ScanBar({ project, onScanned }: { project: Project; onScanned: () => void }) {
  const [busyAction, setBusyAction] = useState<"preflight" | "scan" | "">("");
  const [result, setResult] = useState<ScanResult | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [error, setError] = useState("");
  const [errorChecks, setErrorChecks] = useState<PreflightCheck[]>([]);
  const noRepo = !project.spec_repo.trim();
  const busy = busyAction !== "";

  const runPreflight = async () => {
    setBusyAction("preflight");
    setError("");
    setErrorChecks([]);
    try {
      setPreflight(await api.issuesPreflight(project.id));
      onScanned();
    } catch (e) {
      setError((e as Error).message || "preflight failed");
      setErrorChecks(checksFromError(e));
    }
    setBusyAction("");
  };

  const scan = async () => {
    setBusyAction("scan");
    setError("");
    setErrorChecks([]);
    try {
      setResult(await api.scanIssues(project.id));
      onScanned();
    } catch (e) {
      setError((e as Error).message || "scan failed");
      setErrorChecks(checksFromError(e));
    }
    setBusyAction("");
  };

  return (
    <section className="scan-bar reveal">
      <div className="scan-text">
        <h2>GitHub issues</h2>
        <p className="muted">
          Hive resolves this repo's open issues. Scan to ingest new issues and re-queue clarified or reopened ones.
        </p>
      </div>
      <div className="scan-actions">
        <div className="scan-buttons">
          <button className="ghost" onClick={runPreflight} disabled={busy || noRepo} title={noRepo ? "set a spec repo first" : undefined}>
            {busyAction === "preflight" ? "checking…" : "preflight"}
          </button>
          <button onClick={scan} disabled={busy || noRepo} title={noRepo ? "set a spec repo first" : undefined}>
            {busyAction === "scan" ? "scanning…" : "scan issues"}
          </button>
        </div>
        {error && (
          <div className="scan-error">
            <span className="form-error">{error}</span>
            <CheckList checks={errorChecks} />
          </div>
        )}
        {preflight && !error && <PreflightSummary result={preflight} />}
        {result && !error && (
          <span className="scan-summary">
            last scan: {result.open_issues} open · {result.resolve_queued} queued
            {(result.attachments_downloaded > 0 || result.attachments_failed > 0) && (
              <> · attachments {result.attachments_downloaded} ok / {result.attachments_failed} failed</>
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

function IssueCard({ ws }: { ws: Workstream }) {
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

const ISSUE_GROUPS: { label: string; statuses: WorkstreamStatus[] }[] = [
  { label: "queued", statuses: ["queued"] },
  { label: "in progress", statuses: ["resolving", "reviewing"] },
  { label: "needs you", statuses: ["blocked_clarity", "rejected"] },
  { label: "done", statuses: ["done", "cancelled"] },
];

function issueSort(group: { statuses: WorkstreamStatus[] }) {
  return (a: Workstream, b: Workstream) => {
    if (group.statuses.includes("queued")) {
      return (a.order ?? Number.MAX_SAFE_INTEGER) - (b.order ?? Number.MAX_SAFE_INTEGER) ||
        (a.issue_number ?? 0) - (b.issue_number ?? 0) ||
        a.created_at - b.created_at;
    }
    return b.created_at - a.created_at;
  };
}

function IssuesView({ workstreams }: { workstreams: Workstream[] }) {
  const issues = workstreams.filter((w) => w.source === "issue");
  return (
    <section className="issues-view">
      {issues.length === 0 && <p className="muted">no issues yet — scan to ingest open GitHub issues</p>}
      {ISSUE_GROUPS.map((group) => {
        const items = issues
          .filter((w) => group.statuses.includes(w.status))
          .sort(issueSort(group));
        if (items.length === 0) return null;
        return (
          <div className="issue-group" key={group.label}>
            <h3 className="issue-group-title">
              {group.label} <span className="col-count">{items.length}</span>
            </h3>
            <div className="issue-grid">
              {items.map((w) => (
                <IssueCard key={w.id} ws={w} />
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function QuestionCard({ q, onAnswered }: { q: Question; onAnswered: () => void }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(false);
    try {
      await api.answerQuestion(q.id, answer.trim());
      onAnswered();
    } catch {
      setError(true);
    }
    setBusy(false);
  };

  return (
    <article className="q-card reveal">
      <header>
        <span className="q-mark">?</span>
        <span className="q-meta">asked {ago(q.created_at)}</span>
      </header>
      <Markdown text={q.text} />
      <form onSubmit={submit}>
        <textarea
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          rows={3}
          placeholder="Your answer — it will be distilled into the spec…"
        />
        {error && <p className="form-error">submit failed, try again</p>}
        <button type="submit" disabled={busy || !answer.trim()}>
          {busy ? "sending…" : "answer"}
        </button>
      </form>
    </article>
  );
}

function AnsweredQuestion({ q }: { q: Question }) {
  return (
    <details className="q-answered">
      <summary>
        <span className="q-summary-text">{q.text.replace(/[#*`>]/g, "").slice(0, 90)}</span>
        <span className="q-meta">{ago(q.answered_at)}</span>
      </summary>
      <Markdown text={q.text} />
      <div className="q-answer">
        <span className="q-answer-label">answer</span>
        <Markdown text={q.answer} />
      </div>
    </details>
  );
}

function HumanTaskCard({ task, onDone }: { task: HumanTask; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const done = async () => {
    setBusy(true);
    try {
      await api.completeHumanTask(task.id);
      onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="todo-card project-todo reveal">
      <header>
        <h3>{task.title}</h3>
        <span className="muted">{ago(task.created_at)}</span>
      </header>
      <Markdown text={task.instructions} />
      <div className="todo-actions">
        <button onClick={done} disabled={busy}>
          {busy ? "marking…" : "mark done"}
        </button>
      </div>
    </article>
  );
}

function FeedbackButtons({ projectId, targetId }: { projectId: string; targetId: string }) {
  const [verdict, setVerdict] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);

  if (sent) return <span className="fb-sent">feedback sent ✓</span>;

  const send = async () => {
    if (!verdict) return;
    await api.feedback(projectId, targetId, verdict, comment.trim());
    setSent(true);
  };

  return (
    <div className="fb">
      <button className={`fb-btn ${verdict === "up" ? "on" : ""}`} onClick={() => setVerdict("up")} title="good result">
        ▲
      </button>
      <button
        className={`fb-btn down ${verdict === "down" ? "on" : ""}`}
        onClick={() => setVerdict("down")}
        title="bad result"
      >
        ▼
      </button>
      {verdict && (
        <>
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="optional comment"
            onKeyDown={(e) => e.key === "Enter" && send()}
          />
          <button className="fb-send" onClick={send}>
            send
          </button>
        </>
      )}
    </div>
  );
}

type TraceRow = {
  line: number;
  event: string;
  detail: string;
  raw: string;
};

function traceRows(text: string): TraceRow[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      try {
        const parsed = JSON.parse(line) as Record<string, unknown>;
        const event = String(parsed.event ?? parsed.type ?? parsed.role ?? "event");
        const detailKeys = ["cmd", "text", "message", "agent_name", "backend", "exit_code", "cost_usd"];
        const detail = detailKeys
          .filter((key) => parsed[key] !== undefined && parsed[key] !== null && parsed[key] !== "")
          .map((key) => `${key}=${String(parsed[key]).slice(0, 220)}`)
          .join(" · ");
        return { line: index + 1, event, detail: detail || JSON.stringify(parsed).slice(0, 320), raw: line };
      } catch {
        return { line: index + 1, event: "raw", detail: line.slice(0, 320), raw: line };
      }
    });
}

function TracePanel({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (trace !== null) return;
    setBusy(true);
    setError("");
    try {
      setTrace(await api.trace(taskId));
    } catch {
      setError("trace unavailable");
    } finally {
      setBusy(false);
    }
  };

  const rows = trace ? traceRows(trace).slice(-80) : [];
  const rawTrace = trace && trace.length > 40000 ? trace.slice(-40000) : trace;

  return (
    <div className="trace-panel">
      <button className="ghost trace-toggle" onClick={toggle} disabled={busy}>
        {open ? "hide trace" : busy ? "loading trace" : "trace"}
      </button>
      {open && (
        <div className="trace-body">
          <div className="trace-tools">
            <a href={`/api/tasks/${taskId}/trace`} target="_blank" rel="noreferrer">
              raw
            </a>
          </div>
          {error && <p className="form-error">{error}</p>}
          {rows.length > 0 && (
            <div className="trace-rows">
              {rows.map((row) => (
                <div className="trace-row" key={`${row.line}-${row.event}`}>
                  <span className="trace-line">{row.line}</span>
                  <span className="trace-event">{row.event}</span>
                  <span className="trace-detail">{row.detail}</span>
                </div>
              ))}
            </div>
          )}
          {rawTrace && <pre className="trace-raw">{rawTrace}</pre>}
        </div>
      )}
    </div>
  );
}

function TaskCard({ task, projectId, onChanged }: { task: Task; projectId: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<Task | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !full) {
      try {
        setFull(await api.task(task.id));
      } catch {
        setFull(task); // fall back to the (possibly truncated) list payload
      }
    }
  };

  const result = (full ?? task).result_text;
  const hasTrace = Boolean((full ?? task).trace_blob);
  const cancellable = task.status === "pending" || task.status === "running";

  const cancel = async () => {
    setCancelling(true);
    try {
      await api.cancelTask(task.id);
      onChanged();
    } finally {
      setCancelling(false);
    }
  };

  return (
    <article className={`task-card task-${task.status}`}>
      <button className="task-head" onClick={toggle}>
        <span className={`chip chip-kind-${task.kind}`}>{task.kind}</span>
        <span className={`task-status st-${task.status}`}>{task.status}</span>
        <span className="task-repo">{repoShort(task.repo)}</span>
        <span className="task-backend">{task.backend}</span>
        <span className="task-nums">
          {task.cost_usd > 0 && <span>{money(task.cost_usd)}</span>}
          <span>{duration(task.started_at, task.finished_at)}</span>
          <span className="task-age">{ago(task.created_at)}</span>
        </span>
      </button>
      {open && (
        <div className="task-body">
          <p className="task-instructions">{task.instructions}</p>
          {result ? (
            <Markdown className="task-result" text={result} />
          ) : (
            <p className="muted">no result yet</p>
          )}
          {hasTrace && <TracePanel taskId={task.id} />}
          {cancellable && (
            <div className="task-actions">
              <button className="ghost quiet" onClick={cancel} disabled={cancelling || task.cancel_requested}>
                {task.cancel_requested ? "cancel requested" : cancelling ? "cancelling" : "cancel"}
              </button>
            </div>
          )}
          <FeedbackButtons projectId={projectId} targetId={task.id} />
        </div>
      )}
    </article>
  );
}

export default function ProjectPage() {
  const { id = "" } = useParams();
  const { data, failed, refresh } = usePoll(() => api.project(id), [id]);
  const { data: resources } = usePoll(() => api.resources(), [], 8000);

  if (!data) {
    return <div className="page">{failed ? <p className="muted">project unreachable</p> : <p className="muted">loading…</p>}</div>;
  }

  const { project, workstreams, tasks, questions, human_tasks, conversations } = data;
  const intakeConversation =
    conversations.find((c) => c.id === project.intake_conversation_id) ??
    [...conversations].sort((a, b) => b.created_at - a.created_at)[0] ??
    null;
  const openQs = questions.filter((q) => q.status === "open").sort((a, b) => b.created_at - a.created_at);
  const answeredQs = questions.filter((q) => q.status === "answered").sort((a, b) => b.answered_at - a.answered_at);
  const openTodos = human_tasks.filter((t) => t.status === "open").sort((a, b) => b.created_at - a.created_at);
  const inboxCount = openQs.length + openTodos.length;
  const sortedTasks = [...tasks].sort((a, b) => b.created_at - a.created_at);
  const wsOrder: Record<string, number> = { active: 0, parked: 1, done: 2 };
  const sortedWs = [...workstreams].sort((a, b) => (wsOrder[a.status] ?? 9) - (wsOrder[b.status] ?? 9));

  const patch = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    refresh();
  };

  const saveSetup = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    refresh();
  };

  const createRepo = async (repoName: string) => {
    await api.createProjectRepo(id, { name: repoName, private: true });
    refresh();
  };

  const startIntake = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    await api.startIntake(id);
    refresh();
  };

  const sendIntakeMessage = async (
    conversationId: string,
    action: "message" | "proceed" | "approve",
    message = "",
  ) => {
    await api.conversationMessage(conversationId, { action, message });
    refresh();
  };

  const configured = Boolean(project.spec_repo.trim());
  const issuesMode = project.work_source === "issues";
  const nonIntakeTasks = tasks.filter((t) => t.kind !== "intake" && t.kind !== "probe");
  const intakeDone = intakeConversation?.status === "done";
  const needsSetup = !issuesMode && (!configured || (workstreams.length === 0 && nonIntakeTasks.length === 0 && !intakeDone));
  const needsStart = false;
  const trustedScouts = (resources?.resources ?? []).filter((resource) =>
    resource.backend === "codex" || resource.backend === "claude",
  );

  const inboxCol = (
    <section className="col col-inbox">
      <h2 className="col-title">
        inbox <span className="col-count">{inboxCount}</span>
      </h2>
      {inboxCount === 0 && <p className="muted">no open questions or todos — the hive is unblocked</p>}
      {openTodos.map((t) => (
        <HumanTaskCard key={t.id} task={t} onDone={refresh} />
      ))}
      {openQs.map((q) => (
        <QuestionCard key={q.id} q={q} onAnswered={refresh} />
      ))}
      {answeredQs.length > 0 && (
        <div className="answered-section">
          <h3>answered</h3>
          {answeredQs.map((q) => (
            <AnsweredQuestion key={q.id} q={q} />
          ))}
        </div>
      )}
    </section>
  );

  const activityCol = (
    <section className="col col-feed">
      <h2 className="col-title">
        activity <span className="col-count">{tasks.length}</span>
      </h2>
      {sortedTasks.length === 0 && <p className="muted">no tasks yet</p>}
      {sortedTasks.map((t) => (
        <TaskCard key={t.id} task={t} projectId={id} onChanged={refresh} />
      ))}
    </section>
  );

  return (
    <div className="page page-project">
      <div className="page-head">
        <h1>
          {project.name}
          {configured && <span className="head-repo">{repoShort(project.spec_repo)}</span>}
        </h1>
        <StateBadge state={project.state} questionCount={openQs.length} />
      </div>

      {needsSetup || needsStart ? (
        <ProjectSetup
          project={project}
          conversation={intakeConversation}
          trustedScouts={trustedScouts}
          onSave={saveSetup}
          onCreateRepo={createRepo}
          onStartIntake={startIntake}
          onConversationMessage={sendIntakeMessage}
        />
      ) : (
        <>
          {project.goal_complete && <GoalBanner project={project} onPatch={patch} />}
          {configured && !needsStart && <TogglesBar project={project} onPatch={patch} />}
          {configured && issuesMode && <ScanBar project={project} onScanned={refresh} />}
          {configured && !needsStart && <ProjectSettings project={project} onPatch={patch} />}

          {issuesMode ? (
            <>
              <h2 className="col-title issues-title">
                issues <span className="col-count">{workstreams.filter((w) => w.source === "issue").length}</span>
              </h2>
              <IssuesView workstreams={workstreams} />
              <div className="columns columns-issues">
                {inboxCol}
                {activityCol}
              </div>
            </>
          ) : (
            <div className="columns">
              <section className="col col-ws">
                <h2 className="col-title">
                  workstreams <span className="col-count">{workstreams.length}</span>
                </h2>
                {sortedWs.length === 0 && <p className="muted">none yet — the supervisor will plan some</p>}
                {sortedWs.map((w) => (
                  <WorkstreamCard key={w.id} ws={w} />
                ))}
              </section>
              {inboxCol}
              {activityCol}
            </div>
          )}
        </>
      )}
    </div>
  );
}
