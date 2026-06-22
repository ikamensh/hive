import { useState, type FormEvent } from "react";
import { repoShort } from "../../api";
import type { Checkout, Directive } from "../../types";

/** The hero of the launchpad: a free-form ask that Hive triages and routes.
 * Routing/dispatch is not wired yet (see wiki/project-launchpad.md) — submitting
 * persists the directive and shows a preview executor suggestion. */
export function DirectiveComposer({
  onSubmit,
}: {
  onSubmit: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const trimmed = text.trim();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      await onSubmit(trimmed);
      setText("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="directive-composer" onSubmit={submit}>
      <div className="directive-composer-head">
        <i className="ti ti-sparkles" aria-hidden />
        <div>
          <h3>Give Hive a task</h3>
          <p className="muted">
            Describe what you want done. Hive will find an executor and a machine.
          </p>
        </div>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder="e.g. Add a dark-mode toggle, or investigate the slow nightly export…"
      />
      <div className="directive-composer-actions">
        <span className="muted preview-tag">preview — routing not yet dispatched</span>
        <button type="submit" disabled={!trimmed || busy}>
          {busy ? "sending…" : "give to Hive"}
        </button>
      </div>
    </form>
  );
}

type JobKind = "issues" | "tests" | "build" | "sync";

interface JobTile {
  kind: JobKind;
  icon: string;
  label: string;
  hint: string;
  disabled?: boolean;
}

/** One-click launchers beside the directive box. Opening issues/tests reveals
 * their full table as a drill-down; build/sync act in place. */
export function JobTiles({
  tiles,
  onLaunch,
}: {
  tiles: JobTile[];
  onLaunch: (kind: JobKind) => void;
}) {
  return (
    <div className="job-tiles">
      {tiles.map((tile) => (
        <button
          key={tile.kind}
          type="button"
          className="job-tile"
          onClick={() => onLaunch(tile.kind)}
          disabled={tile.disabled}
          title={tile.disabled ? "not available on this project yet" : undefined}
        >
          <i className={`ti ${tile.icon}`} aria-hidden />
          <span className="job-tile-label">{tile.label}</span>
          <span className="job-tile-hint">{tile.hint}</span>
        </button>
      ))}
    </div>
  );
}

const DIRECTIVE_STATUS_LABEL: Record<Directive["status"], string> = {
  triaging: "triaging",
  awaiting_executor: "routed (preview)",
  working: "working",
  done: "done",
  cancelled: "cancelled",
};

export function DirectivesList({
  directives,
  machineName,
}: {
  directives: Directive[];
  machineName: (id: string) => string;
}) {
  if (directives.length === 0) return null;
  return (
    <div className="directives-list">
      {directives.map((d) => (
        <article className="directive-card reveal" key={d.id}>
          <header>
            <p className="directive-text">{d.text}</p>
            <span className={`chip chip-directive-${d.status}`}>
              {DIRECTIVE_STATUS_LABEL[d.status]}
            </span>
          </header>
          <div className="directive-routing">
            {d.suggested_backend ? (
              <>
                <i className="ti ti-route" aria-hidden />
                <span>
                  {d.suggested_backend}
                  {d.suggested_machine_id ? ` · ${machineName(d.suggested_machine_id)}` : ""}
                </span>
              </>
            ) : (
              <>
                <i className="ti ti-clock" aria-hidden />
                <span className="muted">{d.routing_note}</span>
              </>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

function driftLabel(c: Checkout): { text: string; tone: "ok" | "warn" | "muted" } {
  if (!c.exists) return { text: "not checked out", tone: "muted" };
  const parts: string[] = [];
  if (c.ahead > 0) parts.push(`${c.ahead} ahead`);
  if (c.dirty) parts.push("dirty");
  if (parts.length > 0) return { text: parts.join(" · "), tone: "warn" };
  if (c.behind > 0) return { text: `${c.behind} behind`, tone: "muted" };
  return { text: "in sync", tone: "ok" };
}

interface MachineCheckouts {
  machineId: string;
  name: string;
  online: boolean;
  rows: { repo: string; checkout: Checkout | null }[];
}

/** Where this project physically lives. Per machine, the project's repos and
 * whether local work has drifted from the remote. Sync is an agent job that is
 * not wired yet — the button shows a preview note. */
export function MachinesPanel({ groups }: { groups: MachineCheckouts[] }) {
  const [previewFor, setPreviewFor] = useState("");

  if (groups.length === 0) {
    return <p className="muted">No machine has reported a checkout of this project's repos yet.</p>;
  }

  return (
    <div className="machines-panel">
      {groups.map((g) => (
        <article className="machine-checkouts" key={g.machineId}>
          <header>
            <i className="ti ti-server-2" aria-hidden />
            <strong>{g.name}</strong>
            <span className={`dot ${g.online ? "dot-online" : "dot-offline"}`} />
            <span className="muted">{g.online ? "online" : "offline"}</span>
          </header>
          {g.rows.map(({ repo, checkout }) => {
            const drift = checkout ? driftLabel(checkout) : { text: "not checked out", tone: "muted" as const };
            const hasDrift = checkout?.exists && (checkout.ahead > 0 || checkout.dirty);
            return (
              <div className="checkout-row" key={repo}>
                <div className="checkout-repo">
                  <span className="checkout-repo-name">{repoShort(repo)}</span>
                  {checkout?.exists && checkout.branch && (
                    <span className="checkout-branch">
                      <i className="ti ti-git-branch" aria-hidden />
                      {checkout.branch}
                    </span>
                  )}
                </div>
                <span className={`drift-badge drift-${drift.tone}`}>{drift.text}</span>
                <span className="checkout-env muted" title="dependency-setup readiness (reserved)">
                  env: {checkout?.env_status ?? "unknown"}
                </span>
                {hasDrift ? (
                  <button type="button" className="ghost sync-btn" onClick={() => setPreviewFor(checkout!.id)}>
                    Sync
                  </button>
                ) : (
                  <span className="sync-spacer" />
                )}
                {previewFor === checkout?.id && (
                  <span className="sync-preview muted">
                    preview — sync agent would consolidate this to main or ask you; not wired yet
                  </span>
                )}
              </div>
            );
          })}
        </article>
      ))}
    </div>
  );
}

export type { JobKind, JobTile, MachineCheckouts };

/** Group a project's checkouts by machine, one row per project repo, so a repo
 * not present on a machine still shows as "not checked out". */
export function groupCheckouts(
  repos: string[],
  checkouts: Checkout[],
  machine: (id: string) => { name: string; online: boolean },
  canonical: (url: string) => string,
): MachineCheckouts[] {
  const machineIds = [...new Set(checkouts.map((c) => c.machine_id))];
  const byKey = new Map(checkouts.map((c) => [`${c.machine_id}:${canonical(c.repo)}`, c]));
  return machineIds.map((machineId) => {
    const meta = machine(machineId);
    return {
      machineId,
      name: meta.name,
      online: meta.online,
      rows: repos.map((repo) => ({
        repo,
        checkout: byKey.get(`${machineId}:${canonical(repo)}`) ?? null,
      })),
    };
  });
}
