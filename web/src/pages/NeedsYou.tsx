import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useOverview } from "../App";
import { ago, api, usePoll } from "../api";
import { Markdown } from "../components/shared";
import type { HumanTodo, OverviewQuestion } from "../types";

function QuestionItem({ q, onAnswered }: { q: OverviewQuestion; onAnswered: () => void }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(false);
    try {
      await api.answerQuestion(q.id, answer.trim());
      onAnswered();
    } catch {
      setError(true);
      setBusy(false);
    }
  };

  return (
    <article className="needs-card needs-card-q reveal">
      <header className="needs-card-head">
        <span className="needs-kind needs-kind-q">question</span>
        <Link to={`/p/${q.project_id}`} className="needs-scope">
          {q.project_name || "project"}
        </Link>
        <span className="muted">asked {ago(q.created_at)}</span>
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
        <div className="needs-card-actions">
          <Link to={`/p/${q.project_id}`} className="ghost-link">
            open project →
          </Link>
          <button type="submit" disabled={busy || !answer.trim()}>
            {busy ? "sending…" : "answer"}
          </button>
        </div>
      </form>
    </article>
  );
}

function TodoItem({
  todo,
  scope,
  assignee,
  canComplete,
  onDone,
}: {
  todo: HumanTodo;
  scope: string;
  assignee?: string;
  canComplete: boolean;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const done = async () => {
    setBusy(true);
    try {
      await api.completeHumanTodo(todo.id);
      onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="needs-card reveal">
      <header className="needs-card-head">
        <span className="needs-kind needs-kind-todo">todo</span>
        {todo.project_id ? (
          <Link to={`/p/${todo.project_id}`} className="needs-scope">
            {scope}
          </Link>
        ) : (
          <span className="needs-scope">{scope}</span>
        )}
        {assignee && (
          <span className="chip" title="only this user can act on it">
            @{assignee}
          </span>
        )}
        <span className="muted">{ago(todo.created_at)}</span>
      </header>
      <h3 className="needs-card-title">{todo.title}</h3>
      <Markdown text={todo.instructions} />
      <div className="needs-card-actions">
        {todo.project_id && (
          <Link to={`/p/${todo.project_id}`} className="ghost-link">
            open project →
          </Link>
        )}
        {canComplete && (
          <button onClick={done} disabled={busy}>
            {busy ? "marking…" : "mark done"}
          </button>
        )}
      </div>
    </article>
  );
}

export default function NeedsYou() {
  const overview = useOverview();
  const { data: todos, refresh: refreshTodos } = usePoll(() => api.humanTodos(), []);
  const { data: projects } = usePoll(() => api.projects(), [], 30000);
  const { data: auth } = usePoll(() => api.me(), [], 30000);
  const { data: members } = usePoll(() => api.users(), [], 30000);

  const myId = auth?.user.id ?? "";
  const isAdmin = auth?.role !== "resource_provider";
  const loginById = new Map((members ?? []).map((m) => [m.user.id, m.user.github_login]));

  const questions = overview.data?.attention.questions ?? [];
  const offers = overview.data?.attention.offers ?? [];
  const openTodos = (todos ?? []).filter((t) => t.status === "open");
  const doneTodos = (todos ?? []).filter((t) => t.status === "done");
  // Yours first: assigned to you, or unassigned (any admin's job) when you're
  // an admin. The rest wait on a specific other user.
  const yourTodos = openTodos.filter(
    (t) => t.assignee_user_id === myId || (isAdmin && !t.assignee_user_id),
  );
  const otherTodos = openTodos.filter((t) => !yourTodos.includes(t));
  const shown = questions.length + openTodos.length;
  // attention.count is the authoritative open total; the embedded lists are capped.
  const total = overview.data?.attention.count ?? shown;
  const hidden = Math.max(0, total - shown);

  const scopeName = (projectId: string) =>
    projectId === "" ? "org-wide" : projects?.find((p) => p.id === projectId)?.name ?? projectId;
  const assigneeName = (t: HumanTodo) =>
    t.assignee_user_id ? loginById.get(t.assignee_user_id) ?? t.assignee_user_id : undefined;
  const canComplete = (t: HumanTodo) => isAdmin || t.assignee_user_id === myId;

  const refreshAll = () => {
    refreshTodos();
    overview.refresh();
  };

  return (
    <div className="page page-needs">
      <div className="page-head">
        <h1>
          Needs you {total > 0 && <span className="badge hot">{total}</span>}
        </h1>
      </div>
      <p className="muted">
        Open questions and human-only todos across every project — answer or clear them here.
      </p>

      {overview.data && total === 0 && (
        <p className="muted needs-empty">Nothing needs you right now — the hive is unblocked.</p>
      )}

      {questions.length > 0 && (
        <section className="needs-section">
          <h2 className="col-title">
            questions <span className="col-count">{questions.length}</span>
          </h2>
          {questions.map((q) => (
            <QuestionItem key={q.id} q={q} onAnswered={refreshAll} />
          ))}
        </section>
      )}

      {yourTodos.length > 0 && (
        <section className="needs-section">
          <h2 className="col-title">
            your todos <span className="col-count">{yourTodos.length}</span>
          </h2>
          {yourTodos.map((t) => (
            <TodoItem
              key={t.id}
              todo={t}
              scope={scopeName(t.project_id)}
              assignee={t.assignee_user_id && t.assignee_user_id !== myId ? assigneeName(t) : undefined}
              canComplete={canComplete(t)}
              onDone={refreshAll}
            />
          ))}
        </section>
      )}

      {otherTodos.length > 0 && (
        <section className="needs-section">
          <h2 className="col-title">
            waiting on others <span className="col-count">{otherTodos.length}</span>
          </h2>
          <p className="muted">
            Assigned to whoever owns the machine or license involved — only they can act.
          </p>
          {otherTodos.map((t) => (
            <TodoItem
              key={t.id}
              todo={t}
              scope={scopeName(t.project_id)}
              assignee={assigneeName(t)}
              canComplete={canComplete(t)}
              onDone={refreshAll}
            />
          ))}
        </section>
      )}

      {hidden > 0 && (
        <p className="muted needs-more">
          +{hidden} more not shown — clear a few above and the rest will surface.
        </p>
      )}

      {offers.length > 0 && (
        <section className="needs-section">
          <h2 className="col-title">
            hive offers <span className="col-count">{offers.length}</span>
          </h2>
          <p className="muted">
            Things Hive can do autonomously but is not allowed to yet — accept from the
            project, or turn on auto testing with a daily budget and it handles them itself.
          </p>
          {offers.map((offer) => (
            <Link key={offer.workstream_id} to={`/p/${offer.project_id}`} className="offer-row">
              <span className={`chip chip-health-${offer.state}`}>{offer.state}</span>
              <span className="offer-project">{offer.project_name}</span>
              <span className="offer-text">
                {offer.summary} {offer.offer}
              </span>
            </Link>
          ))}
        </section>
      )}

      {doneTodos.length > 0 && (
        <details className="answered-fold needs-done">
          <summary>{doneTodos.length} done</summary>
          {doneTodos.map((t) => (
            <div key={t.id} className="answered-row">
              <span>{t.title}</span>
              <span className="muted">{ago(t.done_at)}</span>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}
