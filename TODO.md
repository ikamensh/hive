# Backlog

Deferred gap-closing work (Phase 3). Captured, not yet started — see chat
context for the full gap analysis these came from.

(Gap 11 episode traces and gap 9 iteration.md ownership are done — see the
runner trace upload / `hive trace`, and the iterate path in `api.py` +
`wiki/architecture.md`.)

## Gap 10 — Spec critique in the loop
`hive/workstreams/critique.py` is currently reachable only via `scripts/spec_critique.py`.
Wire it into the running system:
- Orchestrator opens a new project's workstream 0 with a critique run; its
  findings seed the first batch of clarification questions.
- API + CLI + UI action to re-run critique on demand, with staleness tracking
  ("spec changed since last critique").

## Gap 11 follow-up — trace viewer in the web UI
Traces are uploaded and exposed via `GET /api/tasks/{id}/trace` + `hive trace`.
Still TODO: surface them in the web UI (reuse kodo's JSONL viewer) and capture
the `conversations/` gz files, not just `log.jsonl`.

## Gap 6 — Durable-memory enforcement
`commit_to_spec` distillation is voluntary today; if the model skips it, an
answer survives only in loseable history. Options: detect answered questions
not reflected in a spec commit and nudge/require it; or have the control plane
append raw answers to `input-log/` deterministically.

## Gap 9 — iteration.md editing ownership
Story 10 says editing the iteration goal clears completion and wakes the
orchestrator, but `iterate` only sends a free-text note while `iteration.md`
lives in the spec repo. Decide and implement the canonical path: who writes
`iteration.md`, and how hive notices a direct git edit (until GitHub webhooks
land, an idle project gets no heartbeat).

## Subscription recovery flow — consult subscriptions on blocked_resources
The data model now distinguishes durable `Subscription`s (with `licensing_mode`)
from live per-machine agents, and `/api/resources` surfaces
`subscription_candidates` (`hive/control/capacity.py`). Not yet wired into the
control loop: when work is `blocked_resources` (no online usable agent for a
needed backend), the supervisor/orchestrator should consult subscriptions and
act on the licensing mode — self-serve a `portable` credential onto an online
machine, or file a `HumanTask` login for a `machine_bound` one — instead of just
going quiet. This is "subscriptions as a recovery source, not baseline
capacity"; worth an ADR when built (the genuine trade-off vs counting owned
capacity as always-available). See CONTEXT.md (Subscription / Licensing Mode)
and wiki/architecture.md (provider rulebook, user resource policy).

## Issue solving — selectable run scope
The 2026-06-14 live validation target was issues #2-#4, but scanning the repo
also ingested newly-open issue #5 and the deterministic queue started it after
#4. We cancelled #5 from the UI and left it queued. Issue runs now support
selected issues and all-open snapshots; keep validating whether the UI needs
additional stop-after controls for operator-led validation batches.
