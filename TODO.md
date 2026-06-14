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

## Issues mode — reject-then-retry resumes a stale branch
Strict per-issue sequencing makes a *clean* run conflict-free: issue N+1's
resolve task is created only after issue N lands, and `runner.checkout` branches
the new `hive/issue-<N+1>` off freshly-fetched `origin/<default>` (which now
includes N's merge). But when an issue is *re-attempted* after a REJECT (its
`hive/issue-<n>` branch still exists on origin — we keep it for human
debugging), `runner.checkout` resumes that existing branch (`base =
origin/<branch>`) instead of rebasing on the now-newer default branch, so a
retried issue can build against a stale base and reintroduce the conflict.
Fix needs a small decision: on retry, rebase the kept branch onto the latest
default, or branch fresh (and where to preserve the old attempt for debugging).
Handle alongside the reject-retry UX. Refs: `hive/runner.py::checkout` (~line
88), `hive/issues.py::reconcile`/`advance_issues`.
