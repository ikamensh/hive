import { IconBrandOpenai, IconCpu, IconSparkles } from "@tabler/icons-react";
import { ago, countdown } from "../../api";
import type { UsageWindow } from "../../types";
import "./usage.css";

export interface UsageReading {
  id: string;
  backend: string;
  machineName: string;
  usage_source?: string;
  usage_captured_at?: number;
  usage_windows?: UsageWindow[];
}

export function ProviderName({ backend }: { backend: string }) {
  const names: Record<string, string> = { claude: "Claude", codex: "Codex", opencode: "OpenCode", cursor: "Cursor", gemini: "Gemini" };
  const Icon = backend === "codex" ? IconBrandOpenai : backend === "claude" ? IconSparkles : IconCpu;
  return <span className={`provider-name provider-${backend}`}><Icon size={21} stroke={1.65} aria-hidden /><span>{names[backend] ?? backend}</span></span>;
}

function WindowReading({ window: limit, capturedAt }: { window: UsageWindow; capturedAt: number }) {
  const now = Date.now() / 1000;
  const expired = limit.resets_at > 0 && limit.resets_at <= now;
  const old = capturedAt === 0 || now - capturedAt > 900;
  const remaining = Math.max(0, Math.min(100, 100 - limit.used_percent));
  const label = [limit.model_scope || "Shared", limit.kind.replaceAll("_", " ")].join(" · ");
  return <div className={`usage-window ${expired || old ? "usage-stale" : ""}`}>
    <div className="usage-window-label"><span>{label}</span><strong>{expired ? "Awaiting update" : old ? `${Math.round(remaining)}% last reported` : `${Math.round(remaining)}% remaining`}</strong></div>
    {!expired && <progress max={100} value={remaining} aria-label={`${label}: ${Math.round(remaining)}% ${old ? "last reported" : "remaining"}`} />}
    <div className="usage-window-meta"><span>{capturedAt > 0 ? `Observed ${ago(capturedAt)}` : "Observation time not reported"}</span>
      <span>{expired ? "Reset time passed" : limit.resets_at > 0 ? `Resets in ${countdown(limit.resets_at)}` : "Reset not reported"}</span></div>
  </div>;
}

/** Provider readings remain scoped to their reporting machine; they are never added together. */
export function UsageLimits({ readings, compact = false }: { readings: UsageReading[]; compact?: boolean }) {
  const sorted = [...readings].sort((a, b) => Number(!!b.usage_windows?.length) - Number(!!a.usage_windows?.length) || a.backend.localeCompare(b.backend) || a.machineName.localeCompare(b.machineName));
  const visible = compact ? sorted.slice(0, 2) : sorted;
  return <div className={`usage-limits ${compact ? "usage-compact" : ""}`}>
    {visible.length === 0 ? <p className="empty-copy">Provider limits will appear when an agent reports usage.</p> : visible.map(reading => {
      const windows = reading.usage_windows ?? [];
      const displayed = compact ? windows.slice(0, 1) : windows;
      return <section className="usage-provider" key={reading.id}>
        <div className="usage-provider-head"><ProviderName backend={reading.backend} /><span>{reading.machineName}</span></div>
        {windows.length === 0 ? <p className="usage-unreported">Usage not reported</p> : displayed.map((limit, index) => <WindowReading key={`${limit.kind}:${limit.model_scope}:${index}`} window={limit} capturedAt={reading.usage_captured_at ?? 0} />)}
      </section>;
    })}
    {readings.length > 0 && <p className="usage-footnote">Provider limits can be shared across machines. Readings are not added together.</p>}
  </div>;
}
