# Agent allowances (per-project session grants)

Status: implemented (`hive/_control/allowances.py`; tests in
`tests/test_allowances.py`). Complements `daily_budget_usd` (money) with
count-based, per-project permissions on *which agents* may run and *how many
sessions per day* — the unit that actually meters subscription capacity.

## Why the money budget is not enough

`Project.daily_budget_usd` caps `Task.cost_usd` + `OrchestratorRun.cost_usd`
since UTC midnight. But `Task.cost_usd` comes from the kodo session result, and
subscription-backed CLIs (codex on a ChatGPT plan, Claude Max, cursor) report
zero or unreliable cost — a project running on subscription backends is
effectively uncapped today. The only subscription protection is account-wide
and reactive: provider usage gauges on `Resource.usage_windows` cool a resource
down at ≥98% (`_control/limits.py`). That protects the license, not the
project split — one greedy test project can drain the week's window for
everything else.

Subscriptions are metered in sessions per window, not dollars, so the
per-project limit should be too.

## Model

```python
class AgentGrant(BaseModel):
    """One additive permission to run agent sessions."""
    backends: list[str] = []            # empty = any backend
    models: list[str] = []              # empty = any model (incl. backend default)
    sessions_per_day: int | None = None # None = unlimited

class Project(BaseModel):
    ...
    agent_grants: list[AgentGrant] = []  # empty = anything allowed (today's behavior)
```

- **Session** = one `Task` dispatched to a runner (`started_at >= utc_day_start()`),
  any kind except `probe` (org-level health, negligible). Resolve, review,
  verify, work, intake turns, and test tasks each count 1; retries count (the
  session was consumed). Planner API calls are not agent sessions — the money
  budget covers them.
- **Matching**: task `(backend, model)` matches a grant iff `backends` is
  empty-or-contains the backend and `models` is empty-or-contains the model.
  An empty `task.model` ("backend default") matches only grants with empty
  `models` — a model-restricted grant names its models explicitly, and
  pipelines resolving through such a grant set the model on the task, so
  restricted tasks are always concrete.
- **Grants are additive permissions only** — no deny rules. Restriction is
  expressed by omission (no matching grant = not allowed).

### Combining grants

"5 sessions of anything + unlimited cheap" is two grants:

```json
[
  {"sessions_per_day": 5},
  {"backends": ["codex"], "models": ["gpt-5.4-mini"]}
]
```

Accounting is a stateless recompute from store facts (same philosophy as
`spend_today`): assign today's dispatched tasks in chronological order, each to
a matching grant, preferring unlimited grants, then the one with most remaining.
`remaining(grants, todays_tasks) -> list[int | None]` falls out of the same
pure function. No grant id is stamped on tasks, so editing grants mid-day just
changes the recompute — no migration, no stale references. The function is a
natural property-test target (assignment never exceeds capacity; a task always
lands on the unlimited grant when one matches; order-independence of totals).

### A cheap-only test project

```json
[{"backends": ["gemini-cli"], "models": ["gemini-3-flash-preview"]}]
```

## Enforcement points

1. **`Supervisor.dispatch` — the hard gate.** A pending task whose
   `(backend, model)` has no matching grant with remaining sessions is skipped
   *per task* (an exhausted "any" grant must not block still-allowed cheap
   tasks). Mirrors the `over_budget` skip.
2. **`compute_state`** — pending tasks that only grants block roll up to
   `blocked_budget`, whose meaning stays "daily allowance exhausted; resets at
   UTC midnight". Money and session caps share the state.
3. **`Orchestrator.create_task` — the advisory gate.** Gains a `model`
   parameter (it has none today). A disallowed pair returns an error listing
   the allowed pairs and their remaining counts, so the planner reroutes
   immediately instead of queueing a task that will sit pending until
   midnight. The state snapshot gets an allowance line
   (`ALLOWANCE: any 2/5 left today; codex/gpt-5.4-mini unlimited`).
4. **Deterministic pipelines** (issues, testing, ci, intake) resolve their
   install-level defaults (`Config.issue_backend`, `test_*_backend`,
   `TRUSTED_SCOUTS`) through a shared helper:
   `resolve_agent(project, grants, preferred_backend, preferred_model)
   -> (backend, model) | None` — the preferred pair when allowed, else the
   first allowed pair with remaining sessions (a model-listing grant supplies
   its first model). `None` = nothing allowed right now: the pipeline waits,
   exactly like over-budget. Intake intersects with `TRUSTED_SCOUTS`; an empty
   intersection is the existing 409.

Creation is advisory, dispatch is authoritative — the same split the money
budget uses, and it keeps one choke point.

## Code placement & surfaces

- `hive/_control/allowances.py` — the pure functions (`match`, `assign`,
  `remaining`, `resolve_agent`) over `Project` + today's tasks. No store
  writes.
- `PATCH /api/projects/{id}` accepts `agent_grants`; CLI
  `hive set <project> --grants '<json>'`.
- Read side: the project payload and `hive show limits` gain a per-project
  allowance section (grants + used/remaining today), next to the existing
  license-usage view.

## Live findings (2026-07-09 lab run)

A full new-project loop ran under `[{"sessions_per_day": 4}, {"backends":
["claude"], "models": ["haiku"]}]` on a real runner (only claude usable):
intake picked the granted scout, the planner chose the unlimited haiku pair
for work/verify on its own (ALLOWANCE snapshot line), a directive's resolve
task was remapped codex→claude and held at 0 headroom until the grants were
loosened, then landed. Known gaps observed:

- `goal_complete` wins in `compute_state`, so a grant-blocked pending issue
  task hides behind `idle_goal_complete` — the stall is invisible.
- Pipelines remap by *grants*, not by *fleet availability*: an any-backend
  grant keeps the codex default even when no codex is usable, and the task
  waits as `blocked_resources`. Restricting grants is the current workaround.
- No web UI surface for grants yet (API/CLI/payload only), and `hive new`
  starts intake before grants can be set (create → set → intake/start is the
  restricted-from-birth sequence).

## Future (not now)

- Grants as a share of a subscription window ("20% of the weekly Claude Max
  window") once `estimate_window_budgets` is trustworthy enough to convert
  percent to sessions.
- Per-machine grants (pin a project to a laptop's seat) — dispatch already
  handles machine binding via backend availability; add only if a real case
  appears.
