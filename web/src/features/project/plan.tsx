import { useState, type FormEvent } from "react";
import { api } from "../../api";
import { Markdown } from "../../components/shared";
import type { PlanItem, PlanItemStatus, PlanPayload } from "../../types";

/** The iteration plan panel (wiki/iteration-plan.md).
 *
 * One component, two faces keyed off plan status: a draft renders as the
 * review checklist (edit anything, flip items, one-click "Approve all &
 * start"), an approved/finished plan renders as the execution rail. The
 * emergence↔certainty dial is the human's review depth — both extremes stay
 * one click. */

const ITEM_STATUS_LABEL: Record<PlanItemStatus, string> = {
  proposed: "awaiting review",
  approved: "approved",
  queued: "queued",
  resolving: "building",
  reviewing: "reviewing",
  blocked_clarity: "blocked",
  rejected: "rejected",
  done: "landed",
  cancelled: "cancelled",
};

const RAIL_MARK: Record<PlanItemStatus, string> = {
  proposed: "○",
  approved: "◉",
  queued: "○",
  resolving: "●",
  reviewing: "●",
  blocked_clarity: "⚠",
  rejected: "⚠",
  done: "✓",
  cancelled: "✕",
};

const IN_FLIGHT: PlanItemStatus[] = ["resolving", "reviewing"];
const PARKED: PlanItemStatus[] = ["blocked_clarity", "rejected"];
const TERMINAL: PlanItemStatus[] = ["done", "cancelled"];

function editable(item: PlanItem): boolean {
  return !IN_FLIGHT.includes(item.status) && !TERMINAL.includes(item.status);
}

/** Inline editor for one item's document. Fields mirror the work doc the
 * builder receives, so what you write here is exactly what the agent reads. */
function ItemEditor({ item, onSaved }: { item: PlanItem; onSaved: () => void }) {
  const [title, setTitle] = useState(item.title);
  const [story, setStory] = useState(item.story);
  const [constraints, setConstraints] = useState(item.constraints);
  const [notes, setNotes] = useState(item.notes);
  const [busy, setBusy] = useState(false);
  const canEdit = editable(item);
  const dirty =
    title !== item.title || story !== item.story ||
    constraints !== item.constraints || notes !== item.notes;

  const save = async () => {
    if (!dirty || busy) return;
    setBusy(true);
    try {
      await api.patchPlanItem(item.id, { title, story, constraints, notes });
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  if (!canEdit) {
    return (
      <div className="plan-item-doc">
        {item.story && (
          <p><span className="plan-doc-label">story</span> {item.story}</p>
        )}
        {item.constraints && (
          <p><span className="plan-doc-label">constraints</span> {item.constraints}</p>
        )}
        {item.notes && <Markdown text={item.notes} />}
      </div>
    );
  }
  return (
    <div className="plan-item-editor">
      <label>
        <span>title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        <span>target user story — who can do what once this lands</span>
        <textarea rows={2} value={story} onChange={(e) => setStory(e.target.value)} />
      </label>
      <label>
        <span>constraints — boundaries, not a blueprint</span>
        <textarea rows={2} value={constraints} onChange={(e) => setConstraints(e.target.value)} />
      </label>
      <label>
        <span>notes</span>
        <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
      <div className="plan-editor-actions">
        <button type="button" disabled={!dirty || busy} onClick={save}>
          {busy ? "saving…" : "save"}
        </button>
      </div>
    </div>
  );
}

function ItemRow({
  item,
  reviewing,
  expanded,
  onToggle,
  onChanged,
}: {
  item: PlanItem;
  reviewing: boolean; // plan still draft: show flip controls
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const act = (fn: () => Promise<unknown>) => async () => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      onChanged();
    } finally {
      setBusy(false);
    }
  };
  const parked = PARKED.includes(item.status);
  const authored = item.edited_by_human ? "you edited" : item.authored_by === "human" ? "you" : "hive";

  return (
    <article
      className={`plan-item plan-item-${item.status}${expanded ? " plan-item-open" : ""}`}
      aria-busy={busy}
    >
      <div className="plan-item-row">
        <span
          className={`plan-item-icon${IN_FLIGHT.includes(item.status) ? " plan-pulse" : ""}`}
          aria-hidden
        >
          {RAIL_MARK[item.status]}
        </span>
        <button type="button" className="plan-item-main" onClick={onToggle}>
          <span className="plan-item-order">{item.order + 1}</span>
          <span className={`plan-item-title${item.status === "cancelled" ? " plan-item-struck" : ""}`}>
            {item.title}
          </span>
          <span className={`chip chip-plan-${item.status}`}>{ITEM_STATUS_LABEL[item.status]}</span>
          <span className="plan-item-authored muted">{authored}</span>
        </button>
        <div className="plan-item-actions">
          {reviewing && item.status === "proposed" && (
            <button type="button" title="approve this item" onClick={act(() => api.approvePlanItem(item.id))}>
              <i className="ti ti-check" aria-hidden /> approve
            </button>
          )}
          {reviewing && item.status === "approved" && (
            <button type="button" className="ghost" title="back to unreviewed"
              onClick={act(() => api.unapprovePlanItem(item.id))}>
              undo
            </button>
          )}
          {!reviewing && item.status === "proposed" && (
            <button type="button" title="approve this amendment; it queues immediately"
              onClick={act(() => api.approvePlanItem(item.id))}>
              <i className="ti ti-check" aria-hidden /> approve
            </button>
          )}
          {parked && (
            <button type="button" title="re-queue this item for another attempt"
              onClick={act(() => api.retryPlanItem(item.id))}>
              <i className="ti ti-refresh" aria-hidden /> retry
            </button>
          )}
          {editable(item) && (
            <button type="button" className="ghost plan-item-remove" title="cancel this item"
              aria-label="cancel this item"
              onClick={act(() => api.cancelPlanItem(item.id))}>
              ✕
            </button>
          )}
        </div>
      </div>
      {parked && item.parked_reason && (
        <div className="plan-parked-reason">
          <Markdown text={item.parked_reason} />
        </div>
      )}
      {expanded && <ItemEditor key={item.id + item.status} item={item} onSaved={onChanged} />}
    </article>
  );
}

function AddItemForm({ planId, onAdded }: { planId: string; onAdded: () => void }) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true);
    try {
      await api.addPlanItem(planId, { title: title.trim() });
      setTitle("");
      onAdded();
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="plan-add-item" onSubmit={submit}>
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="add an item — high-level statement, details after"
      />
      <button type="submit" className="ghost" disabled={!title.trim() || busy}>
        <i className="ti ti-plus" aria-hidden /> add
      </button>
    </form>
  );
}

/** Empty state + hand-written start: the AI proposes, or you write. */
function PlanStarter({
  projectId,
  onChanged,
  note,
}: {
  projectId: string;
  onChanged: () => void;
  note?: string;
}) {
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState<"" | "propose" | "write">("");
  const propose = async () => {
    if (busy) return;
    setBusy("propose");
    try {
      await api.proposePlan(projectId);
      onChanged();
    } finally {
      setBusy("");
    }
  };
  const write = async (e: FormEvent) => {
    e.preventDefault();
    if (!goal.trim() || busy) return;
    setBusy("write");
    try {
      await api.createPlan(projectId, goal.trim());
      onChanged();
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="plan-starter">
      {note && <p className="muted">{note}</p>}
      <p className="muted">
        An ordered list of work items; nothing runs until you approve it. Approve blind for
        max autonomy, or inspect and rewrite every item.
      </p>
      <div className="plan-starter-actions">
        <button type="button" onClick={propose} disabled={busy !== ""} aria-busy={busy === "propose"}>
          <i className="ti ti-sparkles" aria-hidden />
          {busy === "propose" ? "asking hive…" : "have Hive propose a plan"}
        </button>
        <form className="plan-starter-manual" onSubmit={write}>
          <input
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="or state the iteration goal and write it yourself…"
          />
          <button type="submit" className="ghost" disabled={!goal.trim() || busy !== ""}>
            start empty plan
          </button>
        </form>
      </div>
    </div>
  );
}

export function PlanPanel({
  projectId,
  payload,
  onChanged,
}: {
  projectId: string;
  payload: PlanPayload | null | undefined;
  onChanged: () => void;
}) {
  const [expandedId, setExpandedId] = useState("");
  const [busy, setBusy] = useState(false);

  if (!payload || payload.plan.status === "abandoned") {
    return (
      <div className="plan-panel plan-panel-empty">
        <PlanStarter
          projectId={projectId}
          onChanged={onChanged}
          note={payload ? "The previous plan was abandoned." : undefined}
        />
      </div>
    );
  }

  const { plan, items } = payload;
  const reviewing = plan.status === "draft";
  const live = items.filter((i) => i.status !== "cancelled");
  const approvedCount = items.filter((i) => i.status === "approved").length;
  const pendingCount = items.filter((i) => i.status === "proposed").length;
  const landed = items.filter((i) => i.status === "done").length;

  const run = (fn: () => Promise<unknown>) => async () => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const statusChip =
    plan.status === "draft" ? (
      <span className="chip chip-plan-review">review · {approvedCount}/{live.length} approved</span>
    ) : plan.status === "approved" ? (
      <span className="chip chip-plan-executing">executing · {landed}/{live.length} landed</span>
    ) : (
      <span className="chip chip-plan-done">complete · {landed} landed</span>
    );

  return (
    <div className={`plan-panel plan-panel-${plan.status}`}>
      <header className="plan-head">
        <div className="plan-goal">
          <i className="ti ti-route" aria-hidden />
          <div>
            <h3>{plan.goal}</h3>
            <p className="muted">
              proposed by {plan.proposed_by === "agent" ? "hive" : "you"}
              {reviewing
                ? " — approve items one by one, or all at once; every field is yours to rewrite"
                : plan.status === "approved"
                  ? " — items land in order; each merge passed a fresh-agent review"
                  : " — hive will propose the next iteration"}
            </p>
          </div>
        </div>
        {statusChip}
      </header>

      <div className="plan-items">
        {items.map((item) => (
          <ItemRow
            key={item.id}
            item={item}
            reviewing={reviewing}
            expanded={expandedId === item.id}
            onToggle={() => setExpandedId(expandedId === item.id ? "" : item.id)}
            onChanged={onChanged}
          />
        ))}
      </div>

      {(reviewing || plan.status === "approved") && (
        <AddItemForm planId={plan.id} onAdded={onChanged} />
      )}

      {reviewing && (
        <footer className="plan-footer">
          <button
            type="button"
            className="plan-approve-all"
            disabled={busy || live.length === 0}
            onClick={run(() => api.approvePlan(plan.id))}
          >
            <i className="ti ti-player-play" aria-hidden />
            {pendingCount > 0 ? `Approve all ${live.length} & start` : "Start plan"}
          </button>
          <button type="button" className="ghost" disabled={busy}
            onClick={run(() => api.abandonPlan(plan.id))}>
            discard plan
          </button>
        </footer>
      )}
      {plan.status === "approved" && (
        <footer className="plan-footer plan-footer-quiet">
          <button type="button" className="ghost" disabled={busy}
            onClick={run(() => api.abandonPlan(plan.id))}>
            abandon plan
          </button>
        </footer>
      )}
      {plan.status === "complete" && (
        <PlanStarter projectId={projectId} onChanged={onChanged} />
      )}
    </div>
  );
}
