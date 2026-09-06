import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  IconFileUpload,
  IconPlus,
  IconSparkles,
  IconX,
} from "@tabler/icons-react";
import { api, usePoll } from "../../api";
import "../project/workspace.css";

type WorkMode = "plan" | "design";

/** Both work entry paths retain the created project on a recoverable setup error. */
export function NewWorkButton({
  projectId = "",
  projectName = "",
  defaultMode,
  onStarted,
  label = "New work",
}: {
  projectId?: string;
  projectName?: string;
  defaultMode?: WorkMode;
  onStarted?: () => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        <IconPlus size={17} aria-hidden />
        {label}
      </button>
      {open && (
        <NewWorkDialog
          projectId={projectId}
          projectName={projectName}
          defaultMode={defaultMode}
          onClose={() => setOpen(false)}
          onStarted={onStarted}
        />
      )}
    </>
  );
}

function NewWorkDialog({
  projectId,
  projectName,
  defaultMode,
  onClose,
  onStarted,
}: {
  projectId: string;
  projectName: string;
  defaultMode?: WorkMode;
  onClose: () => void;
  onStarted?: () => void;
}) {
  const navigate = useNavigate();
  const dialog = useRef<HTMLDialogElement>(null);
  const projects = usePoll(() => api.projects(), [], 30000);
  const [mode, setMode] = useState<WorkMode>(defaultMode ?? "plan");
  const [selected, setSelected] = useState(projectId || "new");
  const [name, setName] = useState("");
  const [repoMode, setRepoMode] = useState("existing");
  const [repoUrl, setRepoUrl] = useState("");
  const [validation, setValidation] = useState("");
  const [goal, setGoal] = useState("");
  const [createdId, setCreatedId] = useState("");
  const [repoCreated, setRepoCreated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    let targetId = selected === "new" ? createdId : selected;
    try {
      if (selected === "new") {
        if (!targetId) {
          const project = await api.createProject({
            name: name.trim(),
            ...(mode === "design" ? { spec_text: goal.trim() } : {}),
          });
          targetId = project.id;
          setCreatedId(targetId);
        }
        if (repoMode === "create" && !repoCreated) {
          await api.createProjectRepo(targetId, { private: true });
          setRepoCreated(true);
        }
        await api.patchProject(targetId, {
          name: name.trim(),
          validation_command: validation.trim(),
          ...(repoMode === "existing"
            ? { spec_repo: repoUrl.trim(), member_repos: [repoUrl.trim()] }
            : {}),
        });
      }
      if (mode === "design") {
        if (selected === "new")
          await api.startIntake(targetId, "", goal.trim());
        else {
          const detail = await api.project(targetId);
          if (detail.plan?.plan.status === "approved")
            throw new Error(
              "This project already has an executing plan. Finish or stop it before designing the next one.",
            );
          await api.startIntake(targetId, "", goal.trim());
        }
      }
      navigate(
        `/p/${targetId}?tab=${mode === "design" ? "conversation" : "work"}${mode === "plan" ? "&new=plan" : ""}`,
      );
      onStarted?.();
      onClose();
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog
      ref={dialog}
      className="new-work-dialog"
      aria-labelledby="new-work-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <header>
        <div>
          <h2 id="new-work-title">New work</h2>
          <p>Bring a plan or work out the next step with Hive.</p>
        </div>
        <button
          type="button"
          className="ghost icon-button"
          aria-label="Close new work"
          onClick={onClose}
          disabled={busy}
        >
          <IconX size={20} />
        </button>
      </header>
      <form onSubmit={submit}>
        <div className="work-entry-options" aria-label="How to start">
          <button
            type="button"
            className={mode === "plan" ? "selected" : ""}
            aria-pressed={mode === "plan"}
            onClick={() => setMode("plan")}
            disabled={busy || !!createdId}
          >
            <IconFileUpload size={22} aria-hidden />
            <strong>Bring a plan</strong>
            <span>Upload or paste prepared work.</span>
          </button>
          <button
            type="button"
            className={mode === "design" ? "selected" : ""}
            aria-pressed={mode === "design"}
            onClick={() => setMode("design")}
            disabled={busy || !!createdId}
          >
            <IconSparkles size={22} aria-hidden />
            <strong>Design with AI</strong>
            <span>Start with a goal, even a broad one.</span>
          </button>
        </div>
        <label>
          Project
          <select
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            disabled={busy || !!createdId}
          >
            <option value="new">New project</option>
            {projectId &&
              !projects.data?.some((project) => project.id === projectId) && (
                <option value={projectId}>{projectName || projectId}</option>
              )}
            {projects.data?.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        {projects.failed && (
          <p className="form-error" role="alert">
            Could not load existing projects. You can retry opening this form or
            create a new project.
          </p>
        )}
        {selected === "new" && (
          <>
            <label>
              Project name
              <input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={busy}
                placeholder="e.g. Garden planner"
              />
            </label>
            <label>
              Repository
              <select
                value={repoMode}
                onChange={(event) => setRepoMode(event.target.value)}
                disabled={busy || !!createdId}
              >
                <option value="existing">Connect an existing repository</option>
                <option value="create">Create a private repository</option>
              </select>
            </label>
            {repoMode === "existing" && (
              <label>
                Repository URL
                <input
                  value={repoUrl}
                  onChange={(event) => setRepoUrl(event.target.value)}
                  disabled={busy}
                  placeholder="https://github.com/owner/repository"
                  required
                />
              </label>
            )}
            <label>
              Validation command <span className="muted">(optional)</span>
              <input
                value={validation}
                onChange={(event) => setValidation(event.target.value)}
                disabled={busy}
                placeholder="e.g. npm test"
              />
            </label>
          </>
        )}
        {mode === "design" && (
          <label>
            What do you want to achieve?
            <textarea
              required
              rows={5}
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              disabled={busy || !!createdId}
              placeholder="Describe the outcome. Hive will inspect the repository, surface assumptions, and help define a useful next step."
            />
          </label>
        )}
        <p className="work-entry-note">
          {mode === "design"
            ? "Hive will research and prepare a proposal. You review the plan before code execution starts."
            : "Next, upload or paste your plan and review the accepted work before starting it."}
        </p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button
            type="button"
            className="ghost"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={
              busy ||
              (selected === "new" &&
                (!name.trim() ||
                  (repoMode === "existing" && !repoUrl.trim()))) ||
              (mode === "design" && !goal.trim())
            }
          >
            {busy
              ? "Preparing…"
              : createdId
                ? "Retry setup"
                : mode === "design"
                  ? "Start designing"
                  : "Continue to plan"}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
