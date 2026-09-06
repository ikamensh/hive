import { useState, type FormEvent } from "react";
import { api } from "../../api";

/** Keep the source intact; the chief owns parsing, revisions and duplicate submissions. */
export function PlanSubmission({ projectId, onAccepted, amendment = false }: {
  projectId: string;
  onAccepted: (planId: string) => void;
  amendment?: boolean;
}) {
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const accepted = await api.submitPlan(projectId, source);
      onAccepted(accepted.plan.id);
      setSource("");
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="plan-submission" onSubmit={submit} aria-busy={busy}>
      <h3>{amendment ? "Submit an amendment" : "Submit a plan"}</h3>
      <p className="muted">{amendment
        ? "Export this plan, edit future work, then submit the TOML file with its revision and item keys intact. Changed work needs approval again."
        : "Prepare a plan with your coding agent, then upload or paste it here. Review the accepted items before starting execution."}</p>
      <label>Plan file
        <input type="file" accept=".toml,.md,.txt,text/plain,text/markdown" disabled={busy}
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            setBusy(true);
            setError("");
            try { setSource(await file.text()); }
            catch (error) { setError(String(error)); }
            finally { setBusy(false); }
          }} />
      </label>
      <label>Plan document
        <textarea rows={8} value={source} disabled={busy} spellCheck={false}
          onChange={event => setSource(event.target.value)}
          placeholder={'# Goal\nUsers can export CSV data.\n\n## Export rows\nHandle commas, Unicode and empty input. Run make check.'} />
      </label>
      <p className="muted">TOML or Markdown with a # Goal and one ## heading per task. Submission saves a draft; it does not start agents.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="submit" disabled={busy || !source.trim()}>
        {busy ? "Reading plan…" : "Submit for review"}
      </button>
    </form>
  );
}
