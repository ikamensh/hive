import { useEffect, useState } from "react";
import { ago, api, countdown, money, usePoll } from "../api";

function OrgContext() {
  const [text, setText] = useState<string | null>(null);
  const [saved, setSaved] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    api
      .orgContext()
      .then(setText)
      .catch(() => setError(true));
  }, []);

  const save = async () => {
    if (text === null) return;
    try {
      await api.setOrgContext(text);
      setSaved(true);
      setError(false);
    } catch {
      setError(true);
    }
  };

  return (
    <section className="org-context">
      <h2 className="col-title">org context</h2>
      <p className="muted">Shared with every supervisor and agent across all projects.</p>
      {text === null ? (
        <p className="muted">{error ? "unreachable" : "loading…"}</p>
      ) : (
        <>
          <textarea
            value={text}
            rows={8}
            onChange={(e) => {
              setText(e.target.value);
              setSaved(false);
            }}
          />
          <div className="org-actions">
            {error && <span className="form-error">save failed</span>}
            <button onClick={save} disabled={saved}>
              {saved ? "saved" : "save"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export default function Resources() {
  const { data, failed } = usePoll(() => api.resources(), []);
  // 1s ticker so cooldown countdowns feel live between polls.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const runnerName = (id: string) => data?.runners.find((r) => r.id === id)?.name ?? id;

  return (
    <div className="page page-resources">
      <div className="page-head">
        <h1>Resources</h1>
      </div>
      {!data && <p className="muted">{failed ? "unreachable" : "loading…"}</p>}

      {data && (
        <>
          <div className="runner-grid">
            {data.runners.length === 0 && <p className="muted">no runners registered</p>}
            {data.runners.map((r) => (
              <article key={r.id} className={`runner-card ${r.online ? "online" : "offline"}`}>
                <header>
                  <i className="dot" />
                  <h3>{r.name}</h3>
                  <span className="runner-seen">{r.online ? "online" : `last seen ${ago(r.last_seen)}`}</span>
                </header>
                <div className="backend-chips">
                  {r.backends.map((b) => (
                    <span key={b} className="chip">
                      {b}
                    </span>
                  ))}
                </div>
              </article>
            ))}
          </div>

          <table className="res-table">
            <thead>
              <tr>
                <th>backend</th>
                <th>runner</th>
                <th>availability</th>
                <th className="num">tasks</th>
                <th className="num">total cost</th>
              </tr>
            </thead>
            <tbody>
              {data.resources.map((res) => (
                <tr key={res.id}>
                  <td className="mono">{res.backend}</td>
                  <td>{runnerName(res.runner_id)}</td>
                  <td>
                    {res.available ? (
                      <span className="avail ok">available</span>
                    ) : (
                      <span className="avail cool">cooldown {countdown(res.cooldown_until)}</span>
                    )}
                  </td>
                  <td className="num">{res.total_tasks}</td>
                  <td className="num">{money(res.total_cost_usd)}</td>
                </tr>
              ))}
              {data.resources.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    no resources
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}

      <OrgContext />
    </div>
  );
}
