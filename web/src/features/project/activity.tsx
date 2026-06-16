import { useState, type FormEvent } from "react";
import { ago, api, duration, money, repoShort } from "../../api";
import { Markdown } from "../../components/shared";
import type { HumanTodo, Question, Task } from "../../types";

export function QuestionCard({ q, onAnswered }: { q: Question; onAnswered: () => void }) {
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
    }
    setBusy(false);
  };

  return (
    <article className="q-card reveal">
      <header>
        <span className="q-mark">?</span>
        <span className="q-meta">asked {ago(q.created_at)}</span>
      </header>
      <Markdown text={q.text} />
      <form onSubmit={submit}>
        <textarea
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          rows={3}
          placeholder="Your answer - it will be distilled into the spec..."
        />
        {error && <p className="form-error">submit failed, try again</p>}
        <button type="submit" disabled={busy || !answer.trim()}>
          {busy ? "sending..." : "answer"}
        </button>
      </form>
    </article>
  );
}

export function AnsweredQuestion({ q }: { q: Question }) {
  return (
    <details className="q-answered">
      <summary>
        <span className="q-summary-text">{q.text.replace(/[#*`>]/g, "").slice(0, 90)}</span>
        <span className="q-meta">{ago(q.answered_at)}</span>
      </summary>
      <Markdown text={q.text} />
      <div className="q-answer">
        <span className="q-answer-label">answer</span>
        <Markdown text={q.answer} />
      </div>
    </details>
  );
}

export function HumanTodoCard({ task, onDone }: { task: HumanTodo; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const done = async () => {
    setBusy(true);
    try {
      await api.completeHumanTodo(task.id);
      onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="todo-card project-todo reveal">
      <header>
        <h3>{task.title}</h3>
        <span className="muted">{ago(task.created_at)}</span>
      </header>
      <Markdown text={task.instructions} />
      <div className="todo-actions">
        <button onClick={done} disabled={busy}>
          {busy ? "marking..." : "mark done"}
        </button>
      </div>
    </article>
  );
}

function FeedbackButtons({ projectId, targetId }: { projectId: string; targetId: string }) {
  const [verdict, setVerdict] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);

  if (sent) return <span className="fb-sent">feedback sent</span>;

  const send = async () => {
    if (!verdict) return;
    await api.feedback(projectId, targetId, verdict, comment.trim());
    setSent(true);
  };

  return (
    <div className="fb">
      <button className={`fb-btn ${verdict === "up" ? "on" : ""}`} onClick={() => setVerdict("up")} title="good result">
        ^
      </button>
      <button
        className={`fb-btn down ${verdict === "down" ? "on" : ""}`}
        onClick={() => setVerdict("down")}
        title="bad result"
      >
        v
      </button>
      {verdict && (
        <>
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="optional comment"
            onKeyDown={(e) => e.key === "Enter" && send()}
          />
          <button className="fb-send" onClick={send}>
            send
          </button>
        </>
      )}
    </div>
  );
}

type TraceRow = {
  line: number;
  event: string;
  detail: string;
  raw: string;
};

function traceRows(text: string): TraceRow[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      try {
        const parsed = JSON.parse(line) as Record<string, unknown>;
        const event = String(parsed.event ?? parsed.type ?? parsed.role ?? "event");
        const detailKeys = ["cmd", "text", "message", "agent_name", "backend", "exit_code", "cost_usd"];
        const detail = detailKeys
          .filter((key) => parsed[key] !== undefined && parsed[key] !== null && parsed[key] !== "")
          .map((key) => `${key}=${String(parsed[key]).slice(0, 220)}`)
          .join(" - ");
        return { line: index + 1, event, detail: detail || JSON.stringify(parsed).slice(0, 320), raw: line };
      } catch {
        return { line: index + 1, event: "raw", detail: line.slice(0, 320), raw: line };
      }
    });
}

function TracePanel({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (trace !== null) return;
    setBusy(true);
    setError("");
    try {
      setTrace(await api.trace(taskId));
    } catch {
      setError("trace unavailable");
    } finally {
      setBusy(false);
    }
  };

  const rows = trace ? traceRows(trace).slice(-80) : [];
  const rawTrace = trace && trace.length > 40000 ? trace.slice(-40000) : trace;

  return (
    <div className="trace-panel">
      <button className="ghost trace-toggle" onClick={toggle} disabled={busy}>
        {open ? "hide trace" : busy ? "loading trace" : "trace"}
      </button>
      {open && (
        <div className="trace-body">
          <div className="trace-tools">
            <a href={`/api/tasks/${taskId}/trace`} target="_blank" rel="noreferrer">
              raw
            </a>
          </div>
          {error && <p className="form-error">{error}</p>}
          {rows.length > 0 && (
            <div className="trace-rows">
              {rows.map((row) => (
                <div className="trace-row" key={`${row.line}-${row.event}`}>
                  <span className="trace-line">{row.line}</span>
                  <span className="trace-event">{row.event}</span>
                  <span className="trace-detail">{row.detail}</span>
                </div>
              ))}
            </div>
          )}
          {rawTrace && <pre className="trace-raw">{rawTrace}</pre>}
        </div>
      )}
    </div>
  );
}

export function TaskCard({ task, projectId, onChanged }: { task: Task; projectId: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<Task | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !full) {
      try {
        setFull(await api.task(task.id));
      } catch {
        setFull(task);
      }
    }
  };

  const result = (full ?? task).result_text;
  const hasTrace = Boolean((full ?? task).trace_blob);
  const cancellable = task.status === "pending" || task.status === "running";

  const cancel = async () => {
    setCancelling(true);
    try {
      await api.cancelTask(task.id);
      onChanged();
    } finally {
      setCancelling(false);
    }
  };

  return (
    <article className={`task-card task-${task.status}`}>
      <button className="task-head" onClick={toggle}>
        <span className={`chip chip-kind-${task.kind}`}>{task.kind}</span>
        <span className={`task-status st-${task.status}`}>{task.status}</span>
        <span className="task-repo">{repoShort(task.repo)}</span>
        <span className="task-backend">{task.backend}</span>
        <span className="task-nums">
          {task.cost_usd > 0 && <span>{money(task.cost_usd)}</span>}
          <span>{duration(task.started_at, task.finished_at)}</span>
          <span className="task-age">{ago(task.created_at)}</span>
        </span>
      </button>
      {open && (
        <div className="task-body">
          <p className="task-instructions">{task.instructions}</p>
          {result ? (
            <Markdown className="task-result" text={result} />
          ) : (
            <p className="muted">no result yet</p>
          )}
          {hasTrace && <TracePanel taskId={task.id} />}
          {cancellable && (
            <div className="task-actions">
              <button className="ghost quiet" onClick={cancel} disabled={cancelling || task.cancel_requested}>
                {task.cancel_requested ? "cancel requested" : cancelling ? "cancelling" : "cancel"}
              </button>
            </div>
          )}
          <FeedbackButtons projectId={projectId} targetId={task.id} />
        </div>
      )}
    </article>
  );
}
