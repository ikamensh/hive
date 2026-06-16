import { useState, type FormEvent } from "react";
import { RepoListEditor, RepoUrlInput } from "../../components/RepoPicker";
import {
  AUTONOMY_OPTIONS,
  GuessSlider,
  Markdown,
  MODE_OPTIONS,
  SegPicker,
} from "../../components/shared";
import type {
  AgentConversation,
  Autonomy,
  GuessPropensity,
  Mode,
  Project,
  ProjectPatch,
  ResourceInfo,
} from "../../types";

export function buildSetupPatch(fields: {
  specRepo: string;
  memberRepos: string[];
  mode: Mode;
  autonomy: Autonomy;
  guess: GuessPropensity;
  dailyBudget: string;
}): ProjectPatch {
  const budget = parseFloat(fields.dailyBudget);
  return {
    spec_repo: fields.specRepo.trim(),
    member_repos: fields.memberRepos.map((s) => s.trim()).filter(Boolean),
    mode: fields.mode,
    autonomy: fields.autonomy,
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
    .replace(/^[\s>*#`\-0-9.)]+/gm, "")
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

export function ProjectSetup({
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
  const [guess, setGuess] = useState<GuessPropensity>(project.guess_propensity);
  const [dailyBudget, setDailyBudget] = useState(
    project.daily_budget_usd > 0 ? String(project.daily_budget_usd) : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const fields = { specRepo, memberRepos, mode, autonomy, guess, dailyBudget };
  const draft = !project.spec_repo.trim();
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

  const start = async (e: FormEvent) => {
    e.preventDefault();
    if (!specRepo.trim()) {
      setError("spec repo URL is required");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onStartIntake(buildSetupPatch(fields));
    } catch (e) {
      setError((e as Error).message || "intake failed to start");
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
        <h2>{intakeDone ? "Intake complete" : draft ? "Configure intake" : "Project intake"}</h2>
        <p className="muted">
          {draft
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
                {resource.backend === "codex" ? "codex" : "claude"} - {scoutStateLabel(resource)}
              </span>
            ))}
          </div>
        </div>
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
            {busy ? "saving..." : "save"}
          </button>
          <button
            type="submit"
            disabled={busy || !specRepo.trim() || intakeRunning || intakeDone || Boolean(conversation)}
          >
            {busy ? "starting..." : conversation ? "intake started" : "start intake"}
          </button>
        </div>
      </form>
    </section>
  );
}
