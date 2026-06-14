# Backlog

Deferred gap-closing work (Phase 3). Captured, not yet started — see chat
context for the full gap analysis these came from.

(Gap 11 episode traces and gap 9 iteration.md ownership are done — see the
runner trace upload / `hive trace`, and the iterate path in `api.py` +
`wiki/architecture.md`.)

## Gap 10 — Spec critique in the loop
`hive/critique.py` is currently reachable only via `scripts/spec_critique.py`.
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

## Issues mode — selectable scan/run scope
The 2026-06-14 live validation target was issues #2-#4, but scanning the repo
also ingested newly-open issue #5 and the deterministic queue started it after
#4. We cancelled #5 from the UI and left it queued. Decide whether issues mode
should always process every open issue, or whether the scan/run UI should allow
an explicit subset or stop-after issue for validation batches and operator
control.
