# Local sequential-development acceptance — 6 September 2026

Hive completed the ten-item Molding Foundry plan through real builds, fresh reviews,
executable checks and sequential merges. The result is an engineering sandbox with
molding physics, orders/resources, conserved material, quality/failures, operating
metrics, checkpoints, an interactive dashboard, downstream packaging and a small
production-planning challenge. All simulator implementation came through Hive's
coding tasks; the observer supplied briefs, checked results independently and fixed
Hive/Kodo integration problems.

The original vision works in this observed local install. This was also a repair
exercise: several integration defects needed intervention before steady operation.
A separately reviewed follow-up corrects a stale detail introduced by the chief's
automatic completion notes. The original ten-item plan remains complete and immutable.

## Try the result

The simulator checkout is `/Users/ikamen/code-republic/molding-foundry`.

```sh
cd /Users/ikamen/code-republic/molding-foundry
make setup
make check
make dev PORT=8017
```

Open `http://127.0.0.1:8017/app/`. Choose a different free port if occupied; Hive's
local chief uses port 8000. A separately verified instance is also running at
[localhost:60445](http://127.0.0.1:60445/app/).

In **The 40-part delivery challenge**, start with no upgrades and advance 420 s:
both orders arrive late (215 s / 415 s). Select **Faster packaging**, start a new attempt,
and advance 420 s: both arrive on time (154 s / 304 s), spending 600 of 1000 fictional EUR.
A second press alone cannot overcome the packaging bottleneck. The same 0.20 kg resin
becomes 0.18 kg good parts and 0.02 kg runner scrap in all completed strategies.

In **Where capacity pays off**, run the comparison. One press ships 480 parts/hour;
a second press with a separate mold raises this to 960. With 10 s/part packaging,
shipments stay 358 with either press count; a 2 s packaging upgrade raises the
pair to 956. The UI also runs a fixed ten-seed comparison with approximate sampling
intervals and distinguishes model uncertainty from factory accuracy.

## Acceptance evidence

The criteria were recorded before implementation in [metrics.md](metrics.md).

| Criterion | Result in the observed experiment |
| --- | --- |
| H1 Local operation | One command runs a durable local chief and managed runner without GCP or a paid planner key; repeated real restarts retain state. |
| H2 Editable plans | Markdown import, queued edits, appended game scope and an inserted repair reached actual dispatched instructions; a separate follow-up iteration reopens a completed project. |
| H3 Visibility | Plan/watch exposes task, model, elapsed time, selection/wait reasons, explicit review verdict and exact validation SHA. Stopping the watcher leaves work running. |
| H4 Sequence | Every original item was reviewed and merged before its successor began; no observed cross-item execution overlap. |
| H5 Implementation origin | Task/session reports, native commit/push records and branch/merge lineage support Hive-authored application changes. Observer implementation edits: zero. |
| H6 Review gate | All ten original landings have fresh reviews and successful `make check` evidence at the reviewed SHA; merge trees match. Rejected/transport-failed attempts did not land as completed work. |
| H7 Models | Real Astra builds, a substantive Fable review/correction, Opus reviews and a complete Muse build/fresh-review increment ran successfully. |
| H8 Scoped fallback | Shared-account versus model-specific limits are separated; controlled limits select free Muse, and expiry permits a fresh-provider repair on retained work. |
| H9 Honest limits | Two explicitly synthetic 300 s drills are distinct from native telemetry. No natural exhaustion or deliberate quota burning is claimed. |
| H10 Recovery | A controlled stop during unfinished work preserved 22 file hashes/branch; an immutable successor resumed and completed without manual code recovery. |
| H11 Steady progress | Ordinary pickup is measured in seconds. Every observed gap above 120 s has an operator/runtime-repair cause; one early maintenance interval lacks exact pause endpoints. Whole-run continuous eligibility is not claimed. |
| H12 Spending policy | Subscription authentication and the explicitly free Muse model were used; included-only/$0 policy blocks paid API fallback. No usage reset was used. Provider valuations are not audited cash charges. |
| P1 Engineering | Independent thermal/thickness, cycle-overlap, units and clamp references pass; synthetic quality/reliability parameters are clearly distinguished from physical approximations. |
| P2 Conservation | Independent event/resource/material checks pass across molding, packaging, failures, orders and all game strategies. |
| P3 Reproducibility | Seed, pacing, active/blocked/repair checkpoint and full replay checks pass through real HTTP, CLI and actual UI downloads. |
| P4 Planning usefulness | Prewritten shipping oracles, independent energy/cost arithmetic and 55 CLI experiment samples explain baseline, constrained and scaled outcomes. |
| P5 Actual UI | Real browser input/run/pause/speed/save/load/comparison/game flows pass. Desktop/narrow screenshots were inspected; valid flows produce no browser errors. |
| P6 Extension | Packaging shares the original event clock and a 45-line operation reservation module. The 160-line game layer uses ordinary scenarios/control; core physics, event engine and persistence files are unchanged by the game. |

The frozen original run covers **5.54 observed wall hours**, with 27 recorded attempts
(26 executed and one never delivered). Across 15 ordinary pickups, median wait was
**7.65 s**, maximum 14.20 s. Finished build attempts, including failed attempts, had a
12.58-minute median; reviews had a 5.10-minute median. These include tools/checks
and reporting, not provider-only inference. Setup repairs, deliberate drills and
maintenance are included in wall time; this is not a per-model speed benchmark.

The original final reviewed SHA is `d388f38451544deca159478fc2327dcb7ea0bf83`.
Its landing merge is `29a998f967836f16d6ee4e9079dd58776099b58f`; both have tree
`2771bef5923aacb83999458e8395f017e67b0cea`. Later completion notes are separate
commits and are not represented as part of the reviewed implementation tree.
The final simulator gate passes **508 Python tests and 10 browser tests**, plus
lint/format/typecheck/build. A clean setup and a noneditable installed-wheel test
also pass; the wheel includes the fixed challenge data.

Detailed local evidence is under
`~/.local/share/hive/experiments/production-simulator/evidence/`:

- `final-audit/`: frozen task/plan/commit ledger, native provenance, timing and intervention analysis.
- `production-contract-independent/runs/d388f38451544deca159478fc2327dcb7ea0bf83/REPORT.md`: exact gate, installed wheel, extension, API and UI acceptance.
- The same run's `API_REPORT.md`, `manual-ui/REPORT.md` and `ui-checkpoint-comparison/results.json`: actual boundary/replay/interaction evidence.
- `scaling-packaging-independent/`: 15 prewritten capacity cases, 336 invariant snapshots, 55 CLI comparison samples and actual UI evidence.
- `completion-summary-correction/`: the follow-up documentation repair and completion-context rollout.
- [progress.md](progress.md): chronological findings and fixes; entries are observations, not counts of manual interventions.

The documentation follow-up is also complete. Reviewed
`65fb8af2f1bd0a0b914791649fb496f6e8b007eb` matches merge
`1c6f827fc4c8086f57c9b7bc350620f6987600ea`; subsequent main
`642bd2fff3b50feb7e272f992ecbd0884459ed00` records accurate completion notes.
The reviewer fixed two additional stale references in `docs/sources.md`.
Real free-Muse completion received the current ACCEPT and validation SHA, then
recorded the corrected facts; eleven independent delivery/completion checks pass.
All application, build and test files remain identical to the accepted game.
The original ten-item cohort and this one-item follow-up are audited separately.

## What changed in Hive

The final Hive source passes **709 tests (one skip)**, plus focused lint and wheel
build. Local plans now run and recover through the reviewed sequence with clear CLI state,
subscription/free preferences and model-scoped cooldowns. Provider switches preserve
partial checkouts and create compatible fresh sessions; terminal attempt records stay
immutable. Incomplete agent output has explicit outcomes and bounded continuation;
a failed unfinished review can be retried as a fresh review instead of rebuilding.

Muse/OpenCode is eligible for intake, planning, build, review, triage and automatic
testing. Planner tool requests are validated before effects, and isolated OpenCode
configuration pins auxiliary models to the explicitly free choice. Live planner,
builder and fresh-review runs complement integration coverage of the other roles.

Reviews now receive the exact fetched remote default commit and the working checkout
path. A task artifact records the baseline. Shared-host context tells agents to use
available ports, retain their test-service PIDs and stop only those processes. This
is prompt guidance, not an OS sandbox. Claude's bounded SDK buffer was enlarged and
proved with a real image-bearing message above the old limit; transport failures are
classified separately from quota exhaustion and product rejection. The chief now
receives chronological review verdicts and exact validation SHAs, with completion
notes focused on current verified behavior instead of stale historical internals.

## Limits and interpretation

The original run includes 17 documented live-mutation episodes (30 semantic action
groups), including setup, deliberate drills, nine restart episodes and four manual
retries. These are not raw API-call counts and exclude independent observation/tests
and source-only commits. The separate completion-note follow-up is recorded on its
own. Early failures and later repairs remain visible; this is not a claim of an
untouched, zero-intervention first run.

Both same-model and mixed-model reviews missed some boundary cases. Independent
checks found quality-cause attribution and duplicate-JSON issues, then Hive repaired
them through approved work. Fresh sessions and executable tests are useful, but
are not proof that a model cannot share the builder's blind spots.

The simulation is an uncalibrated engineering approximation with explicit synthetic
parameters. Packaging energy, labor, consumables and financial payback are outside
its current scope; investment prices are game rules. Ten-seed intervals quantify
sampling uncertainty inside the model. Replay-based loading grows with saved history.
The large event coordinator remains an architectural deepening opportunity; a third
operation needs explicit schema/transfer/handler work, rather than an automatic plugin.

Small UI limits remain: comparisons scroll horizontally at narrow widths, and loading
a catalog scenario after contract mode requires selecting one first. Contract tests
regenerate tracked walkthrough images, which could dirty a future checkout if browser
rendering changes. These do not invalidate the exercised flows.

Muse can fill every role; there is no role-specific architectural restriction. Its
current Contributor Free offering is temporary and permits training use of prompts
and completions according to [OpenCode Zen](https://opencode.ai/docs/zen/). The model
is configurable. Mixed reviewers remain useful when subscription capacity is available.
