import { useState } from "react";
import { useParams } from "react-router-dom";
import { ago, api, duration, money, repoShort, usePoll } from "../api";
import {
  AUTONOMY_OPTIONS,
  GuessSlider,
  Markdown,
  MODE_OPTIONS,
  SegPicker,
  StateBadge,
} from "../components/shared";
import type { Project, ProjectPatch, Question, Task, Workstream } from "../types";

function TogglesBar({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
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

function GoalBanner({ project, onPatch }: { project: Project; onPatch: (p: ProjectPatch) => void }) {
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

function WorkstreamCard({ ws }: { ws: Workstream }) {
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

function QuestionCard({ q, onAnswered }: { q: Question; onAnswered: () => void }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const submit = async (e: React.FormEvent) => {
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
          placeholder="Your answer — it will be distilled into the spec…"
        />
        {error && <p className="form-error">submit failed, try again</p>}
        <button type="submit" disabled={busy || !answer.trim()}>
          {busy ? "sending…" : "answer"}
        </button>
      </form>
    </article>
  );
}

function AnsweredQuestion({ q }: { q: Question }) {
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

function FeedbackButtons({ projectId, targetId }: { projectId: string; targetId: string }) {
  const [verdict, setVerdict] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);

  if (sent) return <span className="fb-sent">feedback sent ✓</span>;

  const send = async () => {
    if (!verdict) return;
    await api.feedback(projectId, targetId, verdict, comment.trim());
    setSent(true);
  };

  return (
    <div className="fb">
      <button className={`fb-btn ${verdict === "up" ? "on" : ""}`} onClick={() => setVerdict("up")} title="good result">
        ▲
      </button>
      <button
        className={`fb-btn down ${verdict === "down" ? "on" : ""}`}
        onClick={() => setVerdict("down")}
        title="bad result"
      >
        ▼
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

function TaskCard({ task, projectId }: { task: Task; projectId: string }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<Task | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !full) {
      try {
        setFull(await api.task(task.id));
      } catch {
        setFull(task); // fall back to the (possibly truncated) list payload
      }
    }
  };

  const result = (full ?? task).result_text;

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
          <FeedbackButtons projectId={projectId} targetId={task.id} />
        </div>
      )}
    </article>
  );
}

export default function ProjectPage() {
  const { id = "" } = useParams();
  const { data, failed, refresh } = usePoll(() => api.project(id), [id]);

  if (!data) {
    return <div className="page">{failed ? <p className="muted">project unreachable</p> : <p className="muted">loading…</p>}</div>;
  }

  const { project, workstreams, tasks, questions } = data;
  const openQs = questions.filter((q) => q.status === "open").sort((a, b) => b.created_at - a.created_at);
  const answeredQs = questions.filter((q) => q.status === "answered").sort((a, b) => b.answered_at - a.answered_at);
  const sortedTasks = [...tasks].sort((a, b) => b.created_at - a.created_at);
  const wsOrder = { active: 0, parked: 1, done: 2 };
  const sortedWs = [...workstreams].sort((a, b) => wsOrder[a.status] - wsOrder[b.status]);

  const patch = async (p: ProjectPatch) => {
    await api.patchProject(id, p);
    refresh();
  };

  return (
    <div className="page page-project">
      <div className="page-head">
        <h1>
          {project.name}
          <span className="head-repo">{repoShort(project.spec_repo)}</span>
        </h1>
        <StateBadge state={project.state} questionCount={openQs.length} />
      </div>

      {project.goal_complete && <GoalBanner project={project} onPatch={patch} />}
      <TogglesBar project={project} onPatch={patch} />

      <div className="columns">
        <section className="col col-ws">
          <h2 className="col-title">
            workstreams <span className="col-count">{workstreams.length}</span>
          </h2>
          {sortedWs.length === 0 && <p className="muted">none yet — the supervisor will plan some</p>}
          {sortedWs.map((w) => (
            <WorkstreamCard key={w.id} ws={w} />
          ))}
        </section>

        <section className="col col-inbox">
          <h2 className="col-title">
            inbox <span className="col-count">{openQs.length}</span>
          </h2>
          {openQs.length === 0 && <p className="muted">no open questions — the hive is unblocked</p>}
          {openQs.map((q) => (
            <QuestionCard key={q.id} q={q} onAnswered={refresh} />
          ))}
          {answeredQs.length > 0 && (
            <div className="answered-section">
              <h3>answered</h3>
              {answeredQs.map((q) => (
                <AnsweredQuestion key={q.id} q={q} />
              ))}
            </div>
          )}
        </section>

        <section className="col col-feed">
          <h2 className="col-title">
            activity <span className="col-count">{tasks.length}</span>
          </h2>
          {sortedTasks.length === 0 && <p className="muted">no tasks yet</p>}
          {sortedTasks.map((t) => (
            <TaskCard key={t.id} task={t} projectId={id} />
          ))}
        </section>
      </div>
    </div>
  );
}
