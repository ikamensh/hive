import { IconArrowLeft, IconArrowRight, IconGitCommit, IconRefresh } from "@tabler/icons-react";
import { Link, useSearchParams } from "react-router-dom";
import { ago, api, repoShort, usePoll } from "../api";
import type { RecentDelivery } from "../types";
import "./results.css";

const PAGE_SIZE = 20;
const VALIDATION_LABEL: Record<RecentDelivery["validation"]["status"], string> = {
  passed: "Validation passed",
  failed: "Validation failed",
  different_commit: "Checked a different commit",
  not_recorded: "No validation receipt",
};

function DeliveryRow({ delivery }: { delivery: RecentDelivery }) {
  const evidenceUrl = `/p/${encodeURIComponent(delivery.project_id)}?plan=${encodeURIComponent(delivery.plan_id)}&item=${encodeURIComponent(delivery.work_item_id)}`;
  return <article className="results-delivery" aria-label={delivery.title}>
    <div className="results-work">
      <span className="results-commit-icon" aria-hidden><IconGitCommit size={21} /></span>
      <div><h2><Link to={evidenceUrl}>{delivery.title}</Link></h2>
        <p><Link to={`/p/${encodeURIComponent(delivery.project_id)}`}>{delivery.project_name}</Link>
          <span> · </span><time dateTime={new Date(delivery.merged_at * 1000).toISOString()}
            title={new Date(delivery.merged_at * 1000).toLocaleString()}>Landed {ago(delivery.merged_at)}</time></p>
      </div>
    </div>
    <div className={`results-validation validation-${delivery.validation.status}`}>
      <strong>{VALIDATION_LABEL[delivery.validation.status]}</strong>
      {delivery.validation.command && <code>{delivery.validation.command}</code>}
      {delivery.validation.status === "different_commit" && <span>Checked {delivery.validation.commit_sha.slice(0, 10)}</span>}
    </div>
    <div className="results-commit"><code title={delivery.merged_commit_sha}>{delivery.merged_commit_sha.slice(0, 10)}</code>
      <span title={delivery.repo}>{repoShort(delivery.repo)}</span>
    </div>
    <Link className="results-evidence" to={evidenceUrl}>Inspect evidence <IconArrowRight size={14} aria-hidden /></Link>
  </article>;
}

export default function Results() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedOffset = Number(searchParams.get("offset") ?? 0);
  const offset = Number.isSafeInteger(requestedOffset) && requestedOffset >= 0 ? requestedOffset : 0;
  const { data, failed, error, refresh } = usePoll(() => api.deliveries({ offset, limit: PAGE_SIZE }), [offset]);
  const showPage = (nextOffset: number) => setSearchParams(nextOffset > 0 ? { offset: String(nextOffset) } : {});

  return <div className="page page-results">
    <header className="results-heading">
      <div><Link className="results-back" to="/"><IconArrowLeft size={14} aria-hidden /> Overview</Link>
        <h1>Results</h1><p>Recorded landings across your projects. Open the original plan to inspect its evidence.</p></div>
      <button type="button" className="ghost" onClick={refresh}><IconRefresh size={15} aria-hidden /> Refresh</button>
    </header>
    {failed && <div className="results-error" role="alert">
      <span>Could not refresh results: {String(error)}. {data ? "Shown receipts may be out of date." : "Try again to load receipts."}</span>
      <button type="button" className="ghost" onClick={refresh}>Retry</button>
    </div>}
    {!data && !failed && <p className="muted">Loading recorded landings…</p>}
    {data && <>
      <div className="results-summary"><strong>{data.count} recorded {data.count === 1 ? "landing" : "landings"}</strong>
        <span>Observed {ago(data.observed_at)}</span></div>
      {data.items.length === 0 ? <section className="results-empty">
        <IconGitCommit size={30} aria-hidden /><h2>{offset ? "No deliveries on this page" : "No recorded landings yet"}</h2>
        <p>{offset ? "Return to the latest deliveries." : "Landed commits will appear here when work is delivered. Completed attempts alone are not deliveries."}</p>
        {offset > 0 && <button type="button" onClick={() => showPage(0)}>Back to latest</button>}
      </section> : <div className="results-list">
        <div className="results-columns" aria-hidden><span>Delivered work</span><span>Validation evidence</span><span>Landed commit</span><span /></div>
        {data.items.map(delivery => <DeliveryRow delivery={delivery} key={delivery.work_item_id} />)}
      </div>}
      <footer className="results-pagination">
        <span>{data.count > 0 && data.items.length > 0 ? `${offset + 1}–${offset + data.items.length} of ${data.count}` : "No receipts on this page"}</span>
        <div><button type="button" className="ghost" disabled={offset === 0} onClick={() => showPage(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
          <button type="button" className="ghost" disabled={offset + PAGE_SIZE >= data.count} onClick={() => showPage(offset + PAGE_SIZE)}>Next</button></div>
      </footer>
      <p className="results-footnote">A landing records delivered code. It does not by itself establish that the project goal is complete.</p>
    </>}
  </div>;
}
