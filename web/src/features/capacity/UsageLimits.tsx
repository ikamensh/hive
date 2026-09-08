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

const providerNames: Record<string, string> = { claude: "Claude", codex: "Codex", opencode: "OpenCode", cursor: "Cursor", gemini: "Gemini" };
const usageSources: Record<string, string> = { oauth: "Provider API", rollout: "Codex session snapshot", "app-server": "Codex CLI" };

export function ProviderName({ backend }: { backend: string }) {
  const Icon = backend === "codex" ? IconBrandOpenai : backend === "claude" ? IconSparkles : IconCpu;
  return <span className={`provider-name provider-${backend}`}><Icon size={21} stroke={1.65} aria-hidden /><span>{providerNames[backend] ?? backend}</span></span>;
}

function windowPeriod(limit: UsageWindow) {
  if (limit.window_minutes === 300 || limit.kind === "session") return "5-hour";
  if (limit.window_minutes === 10080 || limit.kind.startsWith("weekly")) return "Weekly";
  return limit.kind.replaceAll("_", " ");
}

function WindowReading({ window: limit, label, capturedAt }: { window?: UsageWindow; label: string; capturedAt: number }) {
  if (!limit) return <div className="usage-window usage-unreported" aria-label={`${label} limit`}>
    <div className="usage-window-label"><span>{label}</span><strong>Not reported</strong></div>
  </div>;
  const now = Date.now() / 1000;
  const expired = limit.resets_at > 0 && limit.resets_at <= now;
  const old = capturedAt === 0 || now - capturedAt > 900;
  const remaining = Math.max(0, Math.min(100, 100 - limit.used_percent));
  return <div className={`usage-window ${expired || old ? "usage-stale" : ""}`} aria-label={`${label} limit`}>
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
      const primary = ["claude", "codex"].includes(reading.backend) ? ["5-hour", "Weekly"].map(label => ({
        label, window: windows.find(limit => !limit.model_scope && windowPeriod(limit) === label),
      })) : [];
      const extras = windows.filter(limit => !primary.some(slot => slot.window === limit)).map(limit => ({
        label: [windowPeriod(limit), limit.model_scope].filter(Boolean).join(" · "), window: limit,
      }));
      const slots = [...primary, ...extras];
      const displayed = compact ? slots.slice(0, 2) : slots;
      return <section className="usage-provider" key={reading.id} aria-label={`${providerNames[reading.backend] ?? reading.backend} limits on ${reading.machineName}`}>
        <div className="usage-provider-head"><ProviderName backend={reading.backend} /><span>{reading.machineName}</span></div>
        {windows.length === 0 && <p className="usage-unreported">Usage not reported</p>}
        {displayed.map(slot => <WindowReading key={slot.label} window={slot.window} label={slot.label} capturedAt={reading.usage_captured_at ?? 0} />)}
        {reading.usage_source && <p className="usage-source">Source: {usageSources[reading.usage_source] ?? reading.usage_source}</p>}
      </section>;
    })}
    {readings.length > 0 && <p className="usage-footnote">Provider limits can be shared across machines. Readings are not added together.</p>}
  </div>;
}
