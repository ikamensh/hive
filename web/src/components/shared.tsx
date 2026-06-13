import { marked } from "marked";
import type { Autonomy, GuessPropensity, Mode, ProjectState } from "../types";

marked.setOptions({ gfm: true, breaks: true });

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div
      className={`md ${className}`}
      dangerouslySetInnerHTML={{ __html: marked.parse(text, { async: false }) }}
    />
  );
}

const STATE_META: Record<ProjectState, { label: string; cls: string }> = {
  working: { label: "working", cls: "working" },
  blocked_questions: { label: "needs answers", cls: "questions" },
  blocked_resources: { label: "no resources", cls: "resources" },
  blocked_budget: { label: "budget reached", cls: "resources" },
  idle_goal_complete: { label: "goal complete", cls: "idle" },
  idle_no_workstreams: { label: "idle", cls: "idle" },
};

export function StateBadge({
  state,
  questionCount,
  cooldownHint,
}: {
  state: ProjectState;
  questionCount?: number;
  cooldownHint?: string;
}) {
  const meta = STATE_META[state];
  return (
    <span className={`badge badge-${meta.cls}`}>
      <i className="dot" />
      {meta.label}
      {state === "blocked_questions" && questionCount ? <b>{questionCount}</b> : null}
      {state === "blocked_resources" && cooldownHint ? <b>{cooldownHint}</b> : null}
    </span>
  );
}

export function SegPicker<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          disabled={disabled}
          className={o.value === value ? "on" : ""}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "build", label: "build" },
  { value: "maintain", label: "maintain" },
];

export const AUTONOMY_OPTIONS: { value: Autonomy; label: string }[] = [
  { value: "pr", label: "via PR" },
  { value: "direct_push", label: "direct push" },
];

export const GUESS_LEVELS: GuessPropensity[] = ["never", "rarely", "sometimes", "often", "always"];

export function GuessSlider({
  value,
  onChange,
  disabled,
}: {
  value: GuessPropensity;
  onChange: (v: GuessPropensity) => void;
  disabled?: boolean;
}) {
  const idx = GUESS_LEVELS.indexOf(value);
  return (
    <div className="guess-slider">
      <input
        type="range"
        min={0}
        max={4}
        step={1}
        value={idx}
        disabled={disabled}
        onChange={(e) => onChange(GUESS_LEVELS[Number(e.target.value)])}
      />
      <div className="guess-ticks">
        {GUESS_LEVELS.map((l, i) => (
          <span key={l} className={i === idx ? "on" : ""}>
            {l}
          </span>
        ))}
      </div>
    </div>
  );
}
