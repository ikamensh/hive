import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { RepoListEditor, RepoUrlInput } from "../../components/RepoPicker";
import { Markdown } from "../../components/shared";
import type {
  AgentConversation,
  Project,
  ProjectPatch,
} from "../../types";

export function buildSetupPatch(fields: {
  specRepo: string;
  memberRepos: string[];
}): ProjectPatch {
  return {
    spec_repo: fields.specRepo.trim(),
    member_repos: fields.memberRepos.map((s) => s.trim()).filter(Boolean),
  };
}

function intakeTranscript(conversation: AgentConversation) {
  const turns = conversation.transcript.filter((item) => item.text.trim());
  if (turns.length > 0) return turns;
  if (conversation.latest_brief.trim()) {
    return [{ role: "assistant", text: conversation.latest_brief.trim() }];
  }
  return [];
}

export function ProjectSetup({
  project,
  conversation,
  availableScouts,
  onSave,
  onCreateRepo,
  onStartIntake,
  onFinalizeIntake,
  onConversationMessage,
}: {
  project: Project;
  conversation: AgentConversation | null;
  availableScouts: { backend: string; model: string }[];
  onSave: (patch: ProjectPatch) => Promise<void>;
  onCreateRepo: (repoName: string) => Promise<void>;
  onStartIntake: (patch: ProjectPatch, backend?: string) => Promise<void>;
  onFinalizeIntake: (patch: ProjectPatch) => Promise<void>;
  onConversationMessage: (conversationId: string, action: "message" | "proceed", message?: string) => Promise<void>;
}) {
  const [specRepo, setSpecRepo] = useState(project.spec_repo);
  const [memberRepos, setMemberRepos] = useState(project.member_repos);
  const [repoName, setRepoName] = useState(project.name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-|-$/g, ""));
  const [intakeMessage, setIntakeMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const fields = { specRepo, memberRepos };
  const draft = !project.spec_repo.trim();
  const intakeRunning = conversation?.status === "running" || conversation?.status === "finalizing";
  const intakeDone = conversation?.status === "done";
  const intakeFailed = conversation?.status === "failed";
  const scoutLabel = (scout: { backend: string; model: string }) =>
    [scout.backend, scout.model].filter(Boolean).join(" · ");
  const transcript = conversation ? intakeTranscript(conversation) : [];

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

  const retryIntake = async (backend = "") => {
    setBusy(true);
    setError("");
    try {
      await onStartIntake(buildSetupPatch(fields), backend);
    } catch (e) {
      setError((e as Error).message || "could not retry intake");
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

  const sendConversation = async (action: "message" | "proceed") => {
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

  const finalizeIntake = async () => {
    setBusy(true);
    setError("");
    try {
      await onFinalizeIntake(buildSetupPatch(fields));
    } catch (e) {
      setError((e as Error).message || "could not finalize intake");
    }
    setBusy(false);
  };

  return (
    <section className="setup-panel reveal">
      <header className="setup-head">
        <h2>{intakeDone ? "Agreed project brief" : draft ? "Connect your project" : "Design with Hive"}</h2>
        <p className="muted">
          {draft
            ? "Choose an existing repository or create one for this project."
            : "Shape the goal, review assumptions, and agree on a useful next step."}
        </p>
      </header>
      <form className="setup-form" onSubmit={start}>
        <details open={draft} className="project-disclosure">
        <summary>Connected repositories</summary>
        <label>
          Primary repository
          <RepoUrlInput
            value={specRepo}
            onChange={setSpecRepo}
            placeholder="git@github.com:org/project-spec.git"
          />
        </label>
        <label>
          Other repositories
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
        <button type="button" className="ghost" onClick={save} disabled={busy}>Save repositories</button>
        </details>
        {!conversation && (
          availableScouts.length > 0 ? (
            <p className="scout-status scout-ready">
              Ready to research with {scoutLabel(availableScouts[0])}.
            </p>
          ) : (
            <p className="scout-status scout-blocked">
              No eligible agent is available. Check project settings or <Link to="/capacity">capacity</Link>.
            </p>
          )
        )}
        {error && <p className="form-error">{error}</p>}
        {conversation && (
          <div className={`intake-brief intake-${conversation.status}`}>
            {intakeRunning && <p className="intake-progress" role="status">{conversation.status === "finalizing"
              ? "Saving the agreed specification. Hive will prepare a plan when this finishes."
              : "Hive is inspecting the repository and preparing the next response. You can leave this page and return later."}</p>}
            <header>
              <span className={`chip chip-${conversation.status}`}>{conversation.status}</span>
              <span className="muted">{conversation.backend} {conversation.model}</span>
            </header>
            {intakeFailed && (
              <p className="form-error">
                Design with {scoutLabel(conversation)} could not complete. Retry below or repair the agent in <Link to="/capacity">Capacity</Link>.
              </p>
            )}
            {transcript.length > 0 ? (
              <div className="intake-thread" aria-label="intake conversation">
                {transcript.map((item, index) => (
                  <article className={`intake-turn intake-turn-${item.role || "unknown"}`} key={`${index}-${item.role}`}>
                    <div className="intake-turn-role">{item.role || "message"}</div>
                    <Markdown text={item.text} />
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">waiting for the scout brief</p>
            )}
            {intakeFailed ? (
              <div className="intake-actions">
                <div className="setup-actions">
                  {availableScouts.map((scout) => (
                    <button
                      type="button"
                      key={scout.backend}
                      onClick={() => retryIntake(scout.backend)}
                      disabled={busy}
                    >
                      retry with {scoutLabel(scout)}
                    </button>
                  ))}
                  {availableScouts.length === 0 && (
                    <span className="chip chip-failed">
                      no usable scout — probe or fix an agent in <Link to="/machines">machines</Link>
                    </span>
                  )}
                </div>
              </div>
            ) : (
              !intakeDone && (
                <div className="intake-actions">
                  <textarea
                    value={intakeMessage}
                    onChange={(e) => setIntakeMessage(e.target.value)}
                    rows={4}
                    placeholder="Answer a question, correct an assumption, or refine the goal…"
                    disabled={busy || intakeRunning}
                  />
                  <div className="setup-actions">
                    <button type="button" className="ghost" onClick={() => sendConversation("message")} disabled={busy || intakeRunning || !intakeMessage.trim()}>
                      Send message
                    </button>
                    <button type="button" className="ghost" onClick={() => sendConversation("proceed")} disabled={busy || intakeRunning}>
                      Continue with assumptions
                    </button>
                  </div>
                </div>
              )
            )}
          </div>
        )}
        {!conversation && <div className="setup-actions">
          <button
            type="submit"
            disabled={busy || !specRepo.trim() || intakeRunning || intakeDone || Boolean(conversation)}
          >
            {busy ? "Starting…" : "Start designing"}
          </button>
        </div>}
        {conversation && !intakeDone && !intakeFailed && <div className="setup-actions">
          <button
            type="button"
            onClick={finalizeIntake}
            disabled={busy || intakeRunning || !specRepo.trim()}
          >
            Approve brief &amp; prepare plan
          </button>
        </div>}
      </form>
    </section>
  );
}
