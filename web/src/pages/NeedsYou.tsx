import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { IconArrowRight, IconCheck, IconChevronLeft, IconChevronRight, IconInbox, IconSearch } from "@tabler/icons-react";
import { useOverview } from "../App";
import { ago, api, usePoll } from "../api";
import { Markdown } from "../components/shared";
import type { InboxItem, InboxKind, InboxScope } from "../types";
import "./inbox.css";

const LABELS = { all: "All", question: "Questions", approval: "Approvals", blocked: "Blocked work", todo: "Human tasks" };
const SINGULAR = { question: "Question", approval: "Approval", blocked: "Blocked work", todo: "Human task" };
const PAGE_SIZE = 12;

function ItemDetail({ item, stale, answer, onAnswer, onResolved }: {
  item: InboxItem;
  stale: boolean;
  answer: string;
  onAnswer: (answer: string) => void;
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wholePlan = item.kind === "approval" && !!item.plan && !item.plan_item;
  const review = usePoll(() => api.getPlan(item.plan!.id), [item.plan?.id, wholePlan], 10000, { enabled: wholePlan });
  const projectUrl = `/p/${item.project_id}${item.plan ? `?plan=${encodeURIComponent(item.plan.id)}` : ""}`;
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
      onResolved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The action failed. Try again.");
    } finally {
      setBusy(false);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void act(() => api.answerQuestion(item.question!.id, answer.trim()));
  };
  const disabled = busy || stale;

  return (
    <article className="inbox-detail" aria-label="Selected inbox item">
      <header className="inbox-detail-head">
        <span className={`inbox-kind inbox-kind-${item.kind}`}>{SINGULAR[item.kind]}</span>
        <span className="inbox-meta">{ago(item.created_at)}</span>
      </header>
      <h2>{item.title}</h2>
      <div className="inbox-detail-context">
        {item.project_id ? <Link to={projectUrl}>{item.project_name}</Link> : <span>Workspace</span>}
        {item.assignee_user_id && <span>Assigned to @{item.assignee_name || item.assignee_user_id}</span>}
      </div>
      {item.waiting_on_other && (
        <p className="inbox-owner-note">Waiting on {item.assignee_name ? `@${item.assignee_name}` : "a workspace administrator"}.
          {item.can_act && " You can mark this resolved after confirming the work is done."}</p>
      )}
      <div className="inbox-detail-body"><Markdown text={item.body} /></div>
      {item.todo && Object.keys(item.todo.resolution ?? {}).length > 0 && (
        <p className="inbox-meta">Hive will close this automatically when it observes the issue resolved.</p>
      )}
      {wholePlan && (
        <div className="inbox-plan-review">
          {review.failed ? <p role="alert">Plan details could not be loaded. <button className="ghost" onClick={review.refresh}>Retry</button></p>
            : !review.data ? <p className="inbox-meta">Loading work items…</p>
            : <ol>{review.data.items.filter(i => i.status !== "cancelled").map(i => (
              <li key={i.id}><strong>{i.title}</strong>{i.story && <Markdown text={i.story} />}{i.constraints && <p className="inbox-meta">{i.constraints}</p>}</li>
            ))}</ol>}
        </div>
      )}
      {item.plan_item && item.kind === "approval" && item.plan_item.constraints && <p className="inbox-meta">{item.plan_item.constraints}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {item.question && item.can_act && (
        <form onSubmit={submit} className="inbox-answer">
          <label htmlFor={`answer-${item.question.id}`}>Your answer</label>
          <textarea id={`answer-${item.question.id}`} value={answer} onChange={event => onAnswer(event.target.value)} disabled={busy} rows={5} placeholder="Add the information Hive needs to continue" />
          <button type="submit" disabled={disabled || !answer.trim()}>{busy ? "Sending…" : "Send answer"}<IconArrowRight size={16} /></button>
        </form>
      )}
      <footer className="inbox-detail-actions">
        {item.todo && item.can_act && <button onClick={() => void act(() => api.completeHumanTodo(item.todo!.id))} disabled={disabled}><IconCheck size={16} />{busy ? "Saving…" : "Mark done"}</button>}
        {item.kind === "blocked" && item.plan_item && item.can_act && <button onClick={() => void act(() => api.retryPlanItem(item.plan_item!.id))} disabled={disabled}>{busy ? "Retrying…" : "Retry work"}</button>}
        {item.kind === "approval" && item.can_act && (item.plan_item
          ? <button onClick={() => void act(() => api.approvePlanItem(item.plan_item!.id))} disabled={disabled}>{busy ? "Approving…" : "Approve work item"}</button>
          : <button onClick={() => void act(() => api.approvePlan(item.plan!.id))} disabled={disabled || !review.data || review.failed || review.data.plan.status !== "draft"}>{busy ? "Starting…" : "Approve and start plan"}</button>)}
        {item.project_id && <Link className="inbox-context-link" to={projectUrl}>{item.plan ? "Open plan" : "Open project"}<IconArrowRight size={16} /></Link>}
        {item.todo && (item.todo.kind === "access" || item.todo.kind === "infra") && <Link className="inbox-context-link" to="/machines">Open Capacity<IconArrowRight size={16} /></Link>}
      </footer>
      {!item.can_act && !item.waiting_on_other && <p className="inbox-meta">A workspace administrator can resolve this item.</p>}
    </article>
  );
}

export default function NeedsYou() {
  const overview = useOverview();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = searchParams.get("project") ?? "";
  const [kind, setKind] = useState<InboxKind>("all");
  const [scope, setScope] = useState<InboxScope>("all");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const inbox = usePoll(() => api.inbox({ kind, scope, project_id: projectId, q: query, offset, limit: PAGE_SIZE }), [kind, scope, projectId, query, offset]);
  const selected = inbox.data?.items.find(item => item.id === selectedId) ?? inbox.data?.items[0];
  const total = inbox.data?.total ?? 0;
  useEffect(() => {
    if (inbox.data && !inbox.failed && offset > 0 && offset >= inbox.data.total) {
      setOffset(Math.max(0, Math.floor((inbox.data.total - 1) / PAGE_SIZE) * PAGE_SIZE));
      setSelectedId("");
    }
  }, [inbox.data, inbox.failed, offset]);
  const resetPage = () => { setOffset(0); setSelectedId(""); };
  useEffect(() => { setOffset(0); setSelectedId(""); }, [projectId]);
  const projectName = overview.data?.projects.find(project => project.id === projectId)?.name
    ?? inbox.data?.items[0]?.project_name ?? "Selected project";
  const unfiltered = kind === "all" && scope === "all" && !query && !projectId;
  const refresh = () => {
    void inbox.refresh();
    void overview.refresh();
    setSelectedId("");
  };

  return (
    <div className="page page-inbox">
      <div className="page-head"><div><h1>Inbox <span className="inbox-total">{(projectId ? inbox.data?.total : inbox.data?.counts.all) ?? "—"}</span></h1><p className="muted">Decisions and actions that help Hive move forward.</p></div>
      {projectId && <div className="inbox-project-filter" aria-label="Project filter">
        <span>Project: <strong>{projectName}</strong></span>
        <button type="button" className="ghost" onClick={() => {
          const params = new URLSearchParams(searchParams);
          params.delete("project"); setSearchParams(params); resetPage();
        }}>Show all projects</button>
      </div>}
      </div>
      <div className="inbox-toolbar">
        <label className="inbox-search"><IconSearch size={18} /><input aria-label="Search inbox" type="search" placeholder="Search inbox" value={query} onChange={event => { setQuery(event.target.value); resetPage(); }} /></label>
        <label className="inbox-scope"><span>Assigned to</span><select aria-label="Inbox ownership" value={scope} onChange={event => { setScope(event.target.value as InboxScope); resetPage(); }}><option value="all">Everyone</option><option value="yours">You</option><option value="others">Others</option></select></label>
      </div>
      <div className="inbox-filters" role="group" aria-label="Filter inbox by kind">{(Object.keys(LABELS) as InboxKind[]).map(value => <button key={value} className={kind === value ? "inbox-filter active" : "inbox-filter"} aria-pressed={kind === value} onClick={() => { setKind(value); resetPage(); }}>{LABELS[value]}{!projectId && <span>{inbox.data?.counts[value] ?? "—"}</span>}</button>)}</div>
      {inbox.failed && <div className="inbox-error" role="alert"><span>Inbox could not be refreshed. {inbox.data ? "These items may be out of date." : "Check the chief connection and retry."}</span><button className="ghost" onClick={inbox.refresh}>Retry</button></div>}
      {!inbox.data && !inbox.failed && <p className="inbox-loading" role="status">Loading inbox…</p>}
      {inbox.data && !inbox.failed && total === 0 && <div className="inbox-empty"><IconInbox size={32} /><h2>{unfiltered ? "You're all caught up" : "No matching items"}</h2><p>{unfiltered ? "There are no open questions, approvals, blocked plan items or human tasks." : "Try another filter or search."}</p></div>}
      {inbox.data && total > 0 && <>
        <div className="inbox-workspace">
          <section className="inbox-list" aria-label="Inbox items">{inbox.data.items.map(item => <button key={item.id} className={selected?.id === item.id ? "inbox-row selected" : "inbox-row"} aria-pressed={selected?.id === item.id} onClick={() => setSelectedId(item.id)}>
            <span className="inbox-row-top"><span className={`inbox-kind inbox-kind-${item.kind}`}>{SINGULAR[item.kind]}</span><span>{ago(item.created_at)}</span></span>
            <strong>{item.title}</strong><span className="inbox-row-scope">{item.project_name}{item.waiting_on_other ? ` · Waiting on ${item.assignee_name ? `@${item.assignee_name}` : "admin"}` : ""}</span>
          </button>)}</section>
          {selected && <ItemDetail key={selected.id} item={selected} stale={inbox.failed}
            answer={answers[selected.id] ?? ""}
            onAnswer={answer => setAnswers(previous => ({ ...previous, [selected.id]: answer }))}
            onResolved={() => {
              setAnswers(previous => {
                const next = { ...previous };
                delete next[selected.id];
                return next;
              });
              refresh();
            }} />}
          {!selected && <div className="inbox-empty"><p>No items on this page. Return to the previous page.</p></div>}
        </div>
        <div className="inbox-pagination"><span>{inbox.data.items.length ? offset + 1 : 0}–{Math.min(offset + inbox.data.items.length, total)} of {total} items</span><div><button className="ghost" aria-label="Previous inbox page" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - PAGE_SIZE)); setSelectedId(""); }}><IconChevronLeft size={17} />Previous</button><button className="ghost" aria-label="Next inbox page" disabled={offset + PAGE_SIZE >= total} onClick={() => { setOffset(offset + PAGE_SIZE); setSelectedId(""); }}>Next<IconChevronRight size={17} /></button></div></div>
      </>}
    </div>
  );
}
