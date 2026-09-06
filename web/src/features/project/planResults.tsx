import { api, usePoll } from "../../api";
import { Markdown } from "../../components/shared";
import type { PlanResult } from "../../types";

export function downloadText(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const OUTCOMES: Record<PlanResult["outcome"], string> = {
  pending: "Awaiting approval", running: "Execution in progress", blocked: "Needs attention",
  stopping: "Stopping · waiting for attempts to finish", done: "All items delivered",
  partial: "Partially delivered", abandoned: "Plan abandoned",
};

function commitUrl(repo: string, sha: string): string | undefined {
  const match = repo.match(/^(?:https:\/\/github\.com\/|git@github\.com:)([^/]+)\/([^/]+?)\/?$/);
  if (!match || !/^[a-f0-9]{40,64}$/.test(sha)) return;
  return `https://github.com/${match[1]}/${match[2].replace(/\.git$/, "")}/commit/${sha}`;
}

export function PlanResults({ planId }: { planId: string }) {
  const { data, error, failed, refresh } = usePoll(() => api.planResult(planId), [planId]);
  return (
    <section className="plan-results" aria-label="Execution results">
      {failed && <p role="alert" className="form-error">Could not refresh results: {String(error)}.
        {data && " The snapshot below may be out of date."} <button type="button" className="ghost" onClick={refresh}>Retry</button>
      </p>}
      {!data && !failed && <p className="muted">Loading execution evidence…</p>}
      {data && <>
        <header className="result-head">
          <div>
            <h3>{OUTCOMES[data.outcome]}</h3>
            <p>{data.terminal ? "Execution has ended." : "Execution has not ended."} Assess the goal against the delivered work below.</p>
          </div>
          <button type="button" className="ghost" onClick={() => downloadText(
            `${planId}-result.json`, JSON.stringify(data, null, 2) + "\n", "application/json",
          )}>Download result JSON</button>
        </header>
        <div className="result-counts">
          <strong>{data.counts.done} landed</strong>
          <span>{data.counts.cancelled} cancelled</span>
          <span>{data.counts.remaining} remaining</span>
        </div>
        {data.items.map(item => {
          const url = commitUrl(item.repo, item.merged_commit_sha);
          return (
            <details className="result-item" key={item.id}>
              <summary><span>{item.title}</span><span className={`chip chip-plan-${item.status}`}>
                {item.status === "done" ? "landed" : item.status.replaceAll("_", " ")}
              </span></summary>
              <p className="muted">{item.repo}</p>
              {item.merged_commit_sha && <p>{url
                ? <a href={url} target="_blank" rel="noreferrer">View commit {item.merged_commit_sha.slice(0, 12)}</a>
                : <>Landed commit <code>{item.merged_commit_sha}</code></>}</p>}
              {item.parked_reason && <Markdown text={item.parked_reason} />}
              {item.attempts.length === 0 && <p className="muted">No execution attempts recorded.</p>}
              {item.attempts.map((attempt, index) => (
                <article className="result-attempt" key={attempt.task_id}>
                  <h4>Attempt {index + 1} · {attempt.kind} · {attempt.status}</h4>
                  <p className="muted">{attempt.backend} {attempt.model}
                    {attempt.verdict !== "none" && ` · Review ${attempt.verdict === "accept" ? "accepted" : "rejected"}`}</p>
                  {attempt.result_text && <Markdown text={attempt.result_text} />}
                  {attempt.validation ? (
                    <div className="result-validation">
                      <p><strong>Validation {attempt.validation.exit_code === 0 ? "passed" : "failed"}</strong> · exit {attempt.validation.exit_code}</p>
                      <code>{attempt.validation.command}</code>
                      <p className="muted">{attempt.validation.commit_sha
                        ? <>Checked commit <code>{attempt.validation.commit_sha}</code></>
                        : "No clean, pushed commit recorded for this check."}</p>
                      <pre>{attempt.validation.output}</pre>
                    </div>
                  ) : <p className="muted">No validation command result recorded for this attempt.</p>}
                  {attempt.trace_blob && <a href={`/api/tasks/${attempt.task_id}/trace`} target="_blank" rel="noreferrer">Open agent trace</a>}
                  {attempt.artifact_blobs.length > 0 && <details><summary>Artifact references</summary>
                    {attempt.artifact_blobs.map(blob => <p key={blob}><code>{blob}</code></p>)}
                  </details>}
                </article>
              ))}
            </details>
          );
        })}
        <details className="result-provenance">
          <summary>Snapshot details</summary>
          <p>Plan <code>{data.plan_id}</code> · revision {data.revision}</p>
          <p>Input digest <code>{data.input_digest}</code></p>
          <p>Captured {new Date(data.captured_at * 1000).toLocaleString()}</p>
        </details>
      </>}
    </section>
  );
}
