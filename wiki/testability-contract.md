# The per-project testability contract

Every testing failure mode the 2026-06-17 battle test surfaced on the
environment side — improvised run commands, port collisions, mock-vs-real
fidelity blur, missing run instructions pushing agents into source spelunking —
traces to one gap: **nothing owns "how to stand this app up for testing."**
The testability contract closes it. It is a spec-home artifact, `testability.md`,
that states how to run the product for testing, how to tell it is healthy, and
what decisions or credentials only a human can supply.

The companion principle is **guided setup**: Hive tells the user exactly which
decisions are needed, presents each one with options and a recommendation, and
does everything else itself — exploring the repo, drafting the contract,
proving it works, and folding answers back in. The user's whole job is
answering multiple-choice questions in the existing clarification inbox.

## Division of labor

| Who | Does |
|-----|------|
| Agent (draft task) | Explores the product repo, writes/updates `testability.md`, lists the open decisions with options + recommendation, commits to the spec home |
| Agent (probe task) | Proves the contract on a real runner: stand the app up per the recipe, check health, tear down, report achieved fidelity |
| Chief (deterministic) | Mirrors the file into a `TestabilityContract` record, turns decisions into `Question`s (deduped), chains draft→probe, computes the health verdict, feeds the contract into every sweep/confirm task |
| Human | Answers decision questions; nothing else |

Unlike the story refresh (which is code-blind by design), the draft agent
**must read the code** — the contract is mechanics, not intention. The
code-bias guard stays where it matters: stories say *what to test*, the
contract says *how to run it*.

## The artifact: `testability.md` in the spec home

One file at the spec-home root, peer of `acceptance/`. AI-first authorship,
human-correctable, versioned and reviewable like everything else there.
Format is lenient markdown; only two things are parsed (fidelity subsections
and a content digest), the rest is read by agents, not machines:

```markdown
# testability: my-app

## Run
### local
    npm install
    PORT=$HIVE_TEST_PORT npm run dev
### docker
    docker compose up --build

## Health
GET http://localhost:$PORT/healthz returns 200 within 60s.

## Reset
`npm run seed` restores the demo dataset; state lives only in ./data.

## Credentials & accounts
- STRIPE_TEST_KEY — sandbox key, see decision `stripe-sandbox`.

## Constraints
- No prod access ever; tests run against local/docker only.
```

`### local` / `### docker` subsections under `## Run` declare the available
fidelities. The file is deliberately *not* in the spec digest
(`DIGEST_FILES`/`DIGEST_DIRS` skip the root file), so contract edits never mark
stories stale.

## Decisions: how the human is guided

The draft agent reports, in its structured result, every choice it could not
make alone — a missing sandbox account, two plausible run modes, an ambiguous
reset policy. Each decision is `{key, question, options, recommendation}`.
The chief renders each into a `Question` (the existing clarification inbox:
web NeedsYou page, `hive questions`, project payload), formatted as context +
options + recommendation so it is answerable from where it is shown.

- Dedup is by `Question.dedup_key = "testability:<workstream>:<decision key>"`
  — a re-draft never re-asks an open or already-answered decision.
- Answering a testability question does more than record text: the chief
  queues a fresh draft task whose instructions carry all settled decision
  answers, so the agent folds them into the contract. The user answers;
  Hive edits.
- Unanswered decisions do not block episodes — stories that need the missing
  piece will block individually with the decision named.

## Lifecycle

```
missing ──draft task──▶ draft ──probe task──▶ verified
   ▲                      │  ▲                    │
   └── file deleted       │  └── answer arrives / │
                          │      probe fails →    │
                          ▼      re-draft         ▼
                        broken ◀──── file changed → draft
```

- **draft** — file exists but is unproven against its current content digest.
- **verified** — the last probe succeeded against the current digest; the
  achieved fidelity (`local`/`docker`) is recorded. A file edit moves it back
  to draft (digest mismatch), which schedules a re-probe, not a re-draft.
- **broken** — the last probe against the current digest failed; the probe's
  problems feed the next draft task so the agent repairs the contract, and an
  `env`-kind `HumanTask` (dedup `env:testability:<workstream>`) surfaces
  problems only a human can fix (e.g. Docker daemon down). A later green probe
  auto-resolves it.

Draft completion **auto-queues the probe** — proving the contract is Hive's
job, not a user step.

## Health verdict and the standing offer

`testability_health` is the deterministic sibling of `story_health`, shared by
web/CLI/API: one `state` (`missing | drafting | decisions | draft | probing |
broken | verified`), a human `summary`, Hive's `offer`, and the machine
`action` (`draft | probe | decide | ""`). The offer is always one click/command
away: draft it, probe it, or "answer these N decisions — Hive does the rest."

## Autonomy

`auto_testing_decision` gains contract awareness, inside the same envelope
(testing_auto, positive budget, nothing in flight, per-kind daily cooldown):

1. backlog missing/weak → story refresh (unchanged)
2. contract missing or broken → queue a **draft** task
3. contract draft (unproven, nothing in flight) → queue a **probe** task
4. unproven stories → episode, **but only once the contract is verified** —
   an auto-episode without a proven run recipe is exactly the BLOCKED-noise
   generator the battle test documented. Manual episodes stay ungated.

## Feeding the pipeline

Every `test_sweep`, `test_reproduce`, and `testability_probe` task embeds the
contract text (size-capped) in its instructions under "How to stand the app
up". Sweep agents stop improvising run recipes; fidelity claims are judged
against declared fidelities.

## Data model & task kinds

- `TestabilityContract`: `workstream_id`-keyed mirror of the file — `repo`,
  `content`, `baseline` (content digest), `fidelities`, `status`
  (`missing|draft|verified|broken`), `probed_baseline`, `probed_fidelity`,
  `probe_problems`, `probe_task_id`, `probed_at`, timestamps.
- `Question.dedup_key` — stable identity for decision questions ("" for
  ordinary questions; text-dedup remains their fallback).
- `TaskKind.testability_draft` (smart; reuses `test_refresh_backend`) ends
  `TESTABILITY: DONE|BLOCKED` + structured
  `{changed_files, commit_sha, fidelities, decisions[]}`.
- `TaskKind.testability_probe` (mechanical; reuses `test_confirm_backend`)
  ends `TESTABILITY_PROBE: OK|FAIL` + structured
  `{fidelity, problems[], evidence_blobs[]}`.
- Routes: `POST .../workstreams/{id}/testability-draft` and
  `.../testability-probe`; contract + health ride the project payload
  (`testability` map, per testing workstream). CLI: `hive testability`,
  `hive testability-draft`, `hive testability-probe`.

## Iteration 2 / open

- Per-member-repo contracts (`testability/<slug>.md`) for multi-repo projects;
  today one contract per spec home, matching how `acceptance/` is shared.
- Sandbox credentials as probed resources (Stripe test keys, OAuth test
  accounts) so `Credentials & accounts` entries become dispatchable facts.
- A runner-side testkit (port allocation, health-wait, teardown) the contract
  can reference instead of prose recipes.
- One-click decision answers: `Question.options` rendered as buttons; today
  options live in the question markdown and the answer is free text.
