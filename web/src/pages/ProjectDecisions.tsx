import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, repoShort, usePoll } from "../api";
import { Markdown, StateBadge } from "../components/shared";
import type { DecisionEntry } from "../types";

const SOURCE_ORDER = ["user_provided", "agent_proposed", "code_derived", "inferred"];

function sourceLabel(source: string) {
  if (source === "user_provided") return "operator";
  if (source === "agent_proposed") return "Hive";
  if (source === "code_derived") return "code";
  if (source === "inferred") return "inferred";
  return source.replace(/_/g, " ");
}

function sourceClass(source: string) {
  if (source === "user_provided") return "operator";
  if (source === "agent_proposed") return "hive";
  return "derived";
}

function sortedSources(sources: string[]) {
  return [...sources].sort((a, b) => {
    const ai = SOURCE_ORDER.indexOf(a);
    const bi = SOURCE_ORDER.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) || a.localeCompare(b);
  });
}

function DecisionCard({
  decision,
  busy,
  onReopen,
}: {
  decision: DecisionEntry;
  busy: boolean;
  onReopen: (decision: DecisionEntry) => void;
}) {
  return (
    <article className="decision-card">
      <header>
        <div>
          <span className="decision-id">{decision.id}</span>
          <h2>{decision.title || "Untitled decision"}</h2>
        </div>
        <span className={`decision-source source-${sourceClass(decision.source_type)}`}>
          {sourceLabel(decision.source_type)}
        </span>
      </header>
      <div className="decision-meta">
        <span>impact {decision.impact || "unknown"}</span>
        <span>reversibility {decision.reversibility || "unknown"}</span>
        <span>status {decision.status || "unknown"}</span>
        <span>expires {decision.expires_when || "not recorded"}</span>
      </div>
      {decision.body && <Markdown text={decision.body} className="decision-body" />}
      {decision.trace && <p className="decision-trace">trace: {decision.trace}</p>}
      <div className="decision-actions">
        <button type="button" onClick={() => onReopen(decision)} disabled={!decision.can_reopen || busy}>
          <i className="ti ti-refresh-alert" aria-hidden />
          {busy ? "re-opening..." : "re-open"}
        </button>
      </div>
    </article>
  );
}

export default function ProjectDecisionsPage() {
  const { id = "" } = useParams();
  const { data, failed, refresh } = usePoll(() => api.project(id), [id]);
  const [filter, setFilter] = useState("all");
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  const ledger = data?.decision_ledger;
  const decisions = ledger?.decisions ?? [];
  const filtered = useMemo(
    () => decisions.filter((d) => filter === "all" || d.source_type === filter),
    [decisions, filter],
  );

  if (!data) {
    return <div className="page">{failed ? <p className="muted">project unreachable</p> : <p className="muted">loading...</p>}</div>;
  }

  const { project } = data;
  const sources = sortedSources(ledger?.source_types ?? []);
  const counts = ledger?.counts ?? { total: 0, operator_specified: 0, hive_assumed: 0, reopenable: 0 };

  const reopen = async (decision: DecisionEntry) => {
    setBusyId(decision.id);
    setError("");
    try {
      await api.reopenDecision(project.id, decision.id);
      refresh();
    } catch (e) {
      setError((e as Error).message || "could not re-open decision");
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="page page-decisions">
      <div className="page-head">
        <h1>
          decisions
          <span className="head-repo">{project.name}{project.spec_repo ? ` - ${repoShort(project.spec_repo)}` : ""}</span>
        </h1>
        <StateBadge state={project.state} />
        <Link className="back-link" to={`/p/${id}`}>
          <i className="ti ti-arrow-left" aria-hidden /> Project
        </Link>
      </div>

      <section className="decision-summary">
        <div>
          <span className="summary-label">total</span>
          <strong>{counts.total}</strong>
        </div>
        <div>
          <span className="summary-label">operator</span>
          <strong>{counts.operator_specified}</strong>
        </div>
        <div>
          <span className="summary-label">Hive assumed</span>
          <strong>{counts.hive_assumed}</strong>
        </div>
        <div>
          <span className="summary-label">reopenable</span>
          <strong>{counts.reopenable}</strong>
        </div>
      </section>

      <div className="decision-layout">
        <section className="decision-main">
          <div className="decision-toolbar">
            <div className="decision-filters" role="group" aria-label="Filter decisions by source">
              <button type="button" className={filter === "all" ? "on" : ""} onClick={() => setFilter("all")}>
                all
              </button>
              {sources.map((source) => (
                <button
                  key={source}
                  type="button"
                  className={filter === source ? "on" : ""}
                  onClick={() => setFilter(source)}
                >
                  {sourceLabel(source)}
                </button>
              ))}
            </div>
            {ledger?.error && <span className="decision-error">{ledger.error}</span>}
            {error && <span className="decision-error">{error}</span>}
          </div>

          {filtered.length === 0 ? (
            <p className="muted">no decisions match this filter</p>
          ) : (
            <div className="decision-list">
              {filtered.map((decision) => (
                <DecisionCard
                  key={decision.id}
                  decision={decision}
                  busy={busyId === decision.id}
                  onReopen={reopen}
                />
              ))}
            </div>
          )}
        </section>

        <aside className="decision-authority">
          <h2>must ask</h2>
          <ul>
            {(ledger?.must_ask ?? []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}
