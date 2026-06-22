import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { repoShort } from "../../api";
import { RepoListEditor } from "../../components/RepoPicker";
import {
  AUTONOMY_OPTIONS,
  GuessSlider,
  Markdown,
  MODE_OPTIONS,
  SegPicker,
} from "../../components/shared";
import type { Project, ProjectPatch, WorkItem, Workstream } from "../../types";

type ProjectPatchHandler = (p: ProjectPatch) => void | Promise<void>;

function OverlayPortal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body);
}

function RenameProjectModal({
  project,
  onClose,
  onPatch,
}: {
  project: { name: string };
  onClose: () => void;
  onPatch: ProjectPatchHandler;
}) {
  const [name, setName] = useState(project.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const trimmed = name.trim();
  const canSave = trimmed.length > 0 && trimmed !== project.name && !busy;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSave) return;
    setBusy(true);
    setError("");
    try {
      await onPatch({ name: trimmed });
      onClose();
    } catch {
      setError("rename failed");
      setBusy(false);
    }
  };

  return (
    <OverlayPortal>
      <div className="modal-veil" onClick={onClose}>
        <form className="modal modal-narrow" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
          <h2>Rename project</h2>
          <label>
            project name
            <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
          </label>
          {error && <p className="form-error">{error}</p>}
          <div className="modal-actions">
            <button type="button" className="ghost" onClick={onClose}>
              cancel
            </button>
            <button type="submit" disabled={!canSave}>
              {busy ? "saving..." : "save name"}
            </button>
          </div>
        </form>
      </div>
    </OverlayPortal>
  );
}

function ArchiveProjectModal({
  project,
  onClose,
  onPatch,
}: {
  project: { name: string };
  onClose: () => void;
  onPatch: ProjectPatchHandler;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const archive = async () => {
    setBusy(true);
    setError("");
    try {
      await onPatch({ archived: true, paused: true });
      onClose();
    } catch {
      setError("archive failed");
      setBusy(false);
    }
  };

  return (
    <OverlayPortal>
      <div className="modal-veil" onClick={onClose}>
        <section className="modal modal-narrow" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
          <h2>Archive project?</h2>
          <p className="modal-hint">
            This hides "{project.name}" from the dashboard and pauses it. Project data, history, and repos are not
            deleted.
          </p>
          {error && <p className="form-error">{error}</p>}
          <div className="modal-actions">
            <button type="button" className="ghost" onClick={onClose}>
              cancel
            </button>
            <button type="button" className="danger-fill confirm-action" onClick={archive} disabled={busy}>
              {busy ? "archiving..." : "archive project"}
            </button>
          </div>
        </section>
      </div>
    </OverlayPortal>
  );
}

function ProjectActionSheet({
  project,
  onClose,
  onRename,
  onArchive,
}: {
  project: { name: string };
  onClose: () => void;
  onRename: () => void;
  onArchive: () => void;
}) {
  return (
    <OverlayPortal>
      <div className="modal-veil" onClick={onClose}>
        <section
          className="modal modal-narrow project-action-sheet"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
        >
          <h2>Project actions</h2>
          <p className="modal-hint">{project.name}</p>
          <div className="action-sheet-actions">
            <button type="button" className="ghost" onClick={onRename}>
              Rename
            </button>
            <button type="button" className="danger-outline" onClick={onArchive}>
              Delete / archive...
            </button>
          </div>
          <div className="modal-actions">
            <button type="button" className="ghost" onClick={onClose}>
              cancel
            </button>
          </div>
        </section>
      </div>
    </OverlayPortal>
  );
}

export function ProjectArchiveButton({
  project,
  onPatch,
}: {
  project: { name: string };
  onPatch: ProjectPatchHandler;
}) {
  const [archiveOpen, setArchiveOpen] = useState(false);

  return (
    <>
      <button type="button" className="danger-outline" onClick={() => setArchiveOpen(true)}>
        archive project
      </button>
      {archiveOpen && (
        <ArchiveProjectModal project={project} onPatch={onPatch} onClose={() => setArchiveOpen(false)} />
      )}
    </>
  );
}

export function ProjectActions({
  project,
  onPatch,
  compact = false,
}: {
  project: { name: string };
  onPatch: ProjectPatchHandler;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [menuStyle, setMenuStyle] = useState<CSSProperties | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const update = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const menuWidth = 190;
      const estimatedHeight = 90;
      const margin = 12;
      const below = rect.bottom + 6;
      const maxLeft = Math.max(margin, window.innerWidth - menuWidth - margin);
      const top =
        below + estimatedHeight + margin <= window.innerHeight
          ? below
          : Math.max(margin, rect.top - estimatedHeight - 6);
      const left = Math.min(maxLeft, Math.max(margin, rect.right - menuWidth));
      setMenuStyle({ left, minWidth: menuWidth, top });
    };

    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  const startRename = () => {
    setOpen(false);
    setSheetOpen(false);
    setRenameOpen(true);
  };
  const startArchive = () => {
    setOpen(false);
    setSheetOpen(false);
    setArchiveOpen(true);
  };
  const toggleActions = () => {
    if (compact) {
      setSheetOpen(true);
      return;
    }
    setOpen((v) => !v);
  };

  return (
    <div className={`project-actions ${compact ? "compact" : ""}`} ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        className="project-action-trigger ghost"
        onClick={toggleActions}
        aria-expanded={compact ? sheetOpen : open}
        aria-haspopup={compact ? "dialog" : "menu"}
        title="Project actions"
      >
        {compact ? (
          <span className="project-action-dots" aria-hidden>
            ...
          </span>
        ) : (
          <>
            <i className="ti ti-dots-vertical" aria-hidden />
            <span>Actions</span>
          </>
        )}
      </button>
      {open && menuStyle && (
        <OverlayPortal>
          <div className="project-action-menu" role="menu" ref={menuRef} style={menuStyle}>
            <button type="button" role="menuitem" className="project-action-item" onClick={startRename}>
              <i className="ti ti-pencil" aria-hidden />
              Rename
            </button>
            <button type="button" role="menuitem" className="project-action-item danger" onClick={startArchive}>
              <i className="ti ti-trash" aria-hidden />
              Delete / archive...
            </button>
          </div>
        </OverlayPortal>
      )}
      {sheetOpen && (
        <ProjectActionSheet
          project={project}
          onClose={() => setSheetOpen(false)}
          onRename={startRename}
          onArchive={startArchive}
        />
      )}
      {renameOpen && (
        <RenameProjectModal project={project} onPatch={onPatch} onClose={() => setRenameOpen(false)} />
      )}
      {archiveOpen && (
        <ArchiveProjectModal project={project} onPatch={onPatch} onClose={() => setArchiveOpen(false)} />
      )}
    </div>
  );
}

export function ProjectSettings({
  project,
  workstreams,
  onPatch,
  onPatchWorkstream,
}: {
  project: Project;
  onPatch: ProjectPatchHandler;
  workstreams: Workstream[];
  onPatchWorkstream: (workstreamId: string, patch: { enabled?: boolean }) => Promise<void>;
}) {
  const [memberRepos, setMemberRepos] = useState(project.member_repos);
  const [dailyBudget, setDailyBudget] = useState(
    project.daily_budget_usd > 0 ? String(project.daily_budget_usd) : "",
  );
  const [busy, setBusy] = useState(false);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const budget = parseFloat(dailyBudget);
    const patch: ProjectPatch = {
      member_repos: memberRepos.map((s) => s.trim()).filter(Boolean),
      daily_budget_usd: Number.isFinite(budget) && budget >= 0 ? budget : 0,
    };
    try {
      await onPatch(patch);
    } finally {
      setBusy(false);
    }
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
          {busy ? "saving..." : "save settings"}
        </button>
      </form>
      <div className="workstream-settings">
        <h3>workstreams</h3>
        {workstreams.map((workstream) => (
          <div className="workstream-setting" key={workstream.id}>
            <div>
              <strong>{workstream.title}</strong>
              <span className="muted">
                {workstream.kind.replace(/_/g, " ")}
                {workstream.repo ? ` - ${repoShort(workstream.repo)}` : ""}
              </span>
            </div>
            <button
              type="button"
              className={`switch ${workstream.enabled ? "on" : ""}`}
              onClick={() => onPatchWorkstream(workstream.id, { enabled: !workstream.enabled })}
              disabled={workstream.kind === "iteration"}
              title={workstream.kind === "iteration" ? "iteration work is controlled by project pause" : undefined}
              aria-pressed={workstream.enabled}
            >
              <i />
            </button>
          </div>
        ))}
      </div>
      <div className="settings-danger">
        <ProjectArchiveButton project={project} onPatch={onPatch} />
        <span className="muted">hides it from the list; data is kept</span>
      </div>
    </details>
  );
}

export function TogglesBar({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
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

export function GoalBanner({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
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

export function WorkstreamCard({ ws }: { ws: WorkItem }) {
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
