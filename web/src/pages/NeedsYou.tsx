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

function TodoItem({ todo, scope, onDone }: { todo: HumanTodo; scope: string; onDone: () => void }) {
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
        <button onClick={done} disabled={busy}>
          {busy ? "marking…" : "mark done"}
        </button>
      </div>
    </article>
  );
}

export default function NeedsYou() {
  const overview = useOverview();
  const { data: todos, refresh: refreshTodos } = usePoll(() => api.humanTodos(), []);
  const { data: projects } = usePoll(() => api.projects(), [], 30000);

  const questions = overview.data?.attention.questions ?? [];
  const openTodos = (todos ?? []).filter((t) => t.status === "open");
  const doneTodos = (todos ?? []).filter((t) => t.status === "done");
  const shown = questions.length + openTodos.length;
  // attention.count is the authoritative open total; the embedded lists are capped.
  const total = overview.data?.attention.count ?? shown;
  const hidden = Math.max(0, total - shown);

  const scopeName = (projectId: string) =>
    projectId === "" ? "org-wide" : projects?.find((p) => p.id === projectId)?.name ?? projectId;

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

      {openTodos.length > 0 && (
        <section className="needs-section">
          <h2 className="col-title">
            todos <span className="col-count">{openTodos.length}</span>
          </h2>
          {openTodos.map((t) => (
            <TodoItem key={t.id} todo={t} scope={scopeName(t.project_id)} onDone={refreshAll} />
          ))}
        </section>
      )}

      {hidden > 0 && (
        <p className="muted needs-more">
          +{hidden} more not shown — clear a few above and the rest will surface.
        </p>
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
