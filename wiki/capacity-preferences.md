# Ordered agent capacity

A project's `agent_preferences` is an ordered list of `{backend, model}` pairs.
Dispatch selects the first pair an online, idle runner can serve within the
project's grants and included-only policy. Plan build/review overrides go first;
the project list supplies their alternatives. With no list, an explicit role
remains fixed. Probes and preflight never substitute another backend.

The preference is reconsidered for each pending attempt. Selection and the
actual backend/model are recorded atomically when the task becomes running;
`Task.dispatch_reason` explains why earlier candidates were skipped. Running or
finished attempts never have their model rewritten. A fresh review gets its own
session even when it selects the same model as the builder.

Quota interruption preserves the owning work item or intake conversation, branch,
original runner and dirty checkout, and creates a separate attempt. Transient
provider errors and runner restarts follow the same rule. `retry_of_task_id`
links each attempt to its predecessor; finished attempts retain their own
result and spend, so a late delivery cannot complete a newer attempt. A different backend or model clears
the session handle; provider sessions are never interchangeable. If all choices
are exhausted, the attempt waits and dispatch resumes after capacity returns.
Every dispatched attempt consumes its matching daily session grant.

## Shared and scoped limits

Claude's provider usage payload can contain both shared session/weekly windows
and model-specific windows. `UsageWindow.model_scope` preserves that distinction:
empty means shared; a label such as `fable` or `opus` applies to that model family.
A spent Fable window does not block Opus while the shared windows have headroom.
A spent shared window blocks both. Model limits are additional constraints, not
independent pools of subscription usage.

A rate-limit error cools the whole backend unless its message or provider gauge
identifies a model-specific cap. An explicit shared-window error wins over a
lagging scoped gauge. Scoped error cooldowns appear in `model_cooldowns`; each
exhaustion event records the attempted model and inferred scope. Unknown errors
remain shared. CLI/model version incompatibility is a capability failure, not a
quota reset, and must not be labeled exhaustion.

The limits view retains all provider windows, distinguishes scoped cooldowns,
and counts only matching known-model attempts in scoped token estimates.
Provider gauges include usage outside Hive; Hive's empirical estimates remain
lower bounds rather than subscription entitlements.

## Runtime repairs

An explicit CLI-version/model incompatibility or OpenCode isolation-preflight
refusal is an operational block. Hive records the diagnostic in a runtime
repair todo and keeps the interrupted stage queued as a fresh attempt. It can
continue through configured alternatives with the same checkout and a fresh
provider session. No repair attempt or quota deadline is fabricated.

The simple policy temporarily holds the whole affected CLI on that runner,
even if another model might work on its old version. This avoids repeatedly
testing an incompatible runtime; it does not claim the other model exhausted
its quota. After fixing the runtime/configuration and restarting the runner,
startup and manual probes use the exact failed model. Discovery alone, or a
late successful probe of a different default model, cannot clear the block.
The runtime todo closes when the matching model probe succeeds.

## Verification

`tests/test_capacity_fallback.py` runs the real plan, supervisor and result
processor through scripted Astra → Fable → Opus → free Muse quota failures,
then a separate review and landing. It checks distinct attempts, preserved
checkout/runner/branch, cleared incompatible sessions, grants, capacity status,
reset recovery, selection explanations and intake continuation. Actual editing
is verified separately in the live simulator acceptance experiment; the
scripted protocol test does not prove a model's code quality or local CLI
compatibility.
