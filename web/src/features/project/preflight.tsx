import { ApiError } from "../../api";
import type { PreflightCheck, PreflightResult } from "../../types";

export function checksFromError(error: unknown): PreflightCheck[] {
  const detail = error instanceof ApiError ? error.detail : undefined;
  if (!detail || typeof detail !== "object") return [];
  const checks = (detail as { checks?: unknown }).checks;
  if (!Array.isArray(checks)) return [];
  return checks.filter((check): check is PreflightCheck => {
    if (!check || typeof check !== "object") return false;
    const c = check as Partial<PreflightCheck>;
    return typeof c.name === "string" && typeof c.ok === "boolean" && typeof c.detail === "string";
  });
}

export function CheckList({ checks }: { checks: PreflightCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <ul className="scan-checks">
      {checks.map((check) => (
        <li key={check.name} className={check.ok ? "ok" : check.hard ? "fail" : "warn"}>
          <span>{check.ok ? "pass" : check.hard ? "fail" : "warn"}</span>
          <b>{check.name.replace(/_/g, " ")}</b>
          <small>{check.detail}</small>
        </li>
      ))}
    </ul>
  );
}

export function PreflightSummary({ result }: { result: PreflightResult }) {
  return (
    <div className={`preflight-summary ${result.ok ? "ok" : "blocked"}`}>
      <span>{result.ok ? "preflight passed" : "preflight blocked"}</span>
      {result.runner_check_task && <small>runner check queued in activity</small>}
      <CheckList checks={result.checks} />
    </div>
  );
}
