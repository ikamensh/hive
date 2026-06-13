# Backlog

Deferred gap-closing work (Phase 3). Captured, not yet started — see chat
context for the full gap analysis these came from.

## Gap 10 — Spec critique in the loop
`hive/critique.py` is currently reachable only via `scripts/spec_critique.py`.
Wire it into the running system:
- Orchestrator opens a new project's workstream 0 with a critique run; its
  findings seed the first batch of clarification questions.
- API + CLI + UI action to re-run critique on demand, with staleness tracking
  ("spec changed since last critique").

## Gap 11 — Episode traces
The runner returns only final text + cost. Persist the full kodo JSONL trace:
- Runner uploads the trace blob (GCS / LocalBlobStore) keyed by task id.
- Task records the blob path; API/CLI expose it; UI reuses kodo's JSONL viewer.
- This is also the GEPA training-data substrate (architecture §10).

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
