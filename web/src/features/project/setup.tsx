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
  availableScoutBackends,
  onSave,
  onCreateRepo,
  onStartIntake,
  onFinalizeIntake,
  onConversationMessage,
}: {
  project: Project;
  conversation: AgentConversation | null;
  availableScoutBackends: string[];
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
  const scoutLabel = (backend: string) => (backend === "codex" ? "codex" : "claude");
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
        {!conversation && (
          availableScoutBackends.length > 0 ? (
            <p className="scout-status scout-ready">
              scout ready — intake will run on {scoutLabel(availableScoutBackends[0])}
            </p>
          ) : (
            <p className="scout-status scout-blocked">
              no trusted scout available — probe or fix an agent in <Link to="/machines">machines</Link>
            </p>
          )
        )}
        {error && <p className="form-error">{error}</p>}
        {conversation && (
          <div className={`intake-brief intake-${conversation.status}`}>
            <header>
              <span className={`chip chip-${conversation.status}`}>{conversation.status}</span>
              <span className="muted">{conversation.backend} {conversation.model}</span>
            </header>
            {intakeFailed && (
              <p className="form-error">
                Intake with {scoutLabel(conversation.backend)} could not complete. Retry below, or probe
                or fix an agent in <Link to="/machines">machines</Link>.
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
                  {availableScoutBackends.map((backend) => (
                    <button
                      type="button"
                      key={backend}
                      onClick={() => retryIntake(backend)}
                      disabled={busy}
                    >
                      retry with {scoutLabel(backend)}
                    </button>
                  ))}
                  {availableScoutBackends.length === 0 && (
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
                  </div>
                </div>
              )
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
            {busy ? "starting..." : intakeFailed ? "intake failed" : conversation ? "intake started" : "start intake"}
          </button>
        </div>
        <div className="setup-actions">
          <button
            type="button"
            onClick={finalizeIntake}
            disabled={busy || intakeRunning || !specRepo.trim()}
          >
            approve and finalize
          </button>
        </div>
      </form>
    </section>
  );
}
