# Live acceptance: Hive develops Molding Foundry

Recorded before the first simulator implementation task is dispatched (2026-09-05).
The experiment tests the user's original vision: write and adjust an ordered
plan, then let Hive steadily improve a real project using available subscriptions
and free capacity. The simulator code must be written, reviewed, and landed by
Hive; the observing agent supplies the brief, observes evidence, and fixes Hive.

## Product scope

An extensible injection-molding production simulator, with an engineering model,
production orders/resources, material and quality accounting, deterministic
simulation/replay, and an interactive browser interface for exploring capacity
and scaling. This is a foundation for a realistic production-planning game.
It must have documented physical assumptions and useful reference scenarios;
passing generic application tests alone does not establish simulation quality.
The detailed eight-step plan and sources are recorded beside this document.

## Criteria and proof required

| ID | Criterion | Acceptance evidence |
| --- | --- | --- |
| H1 | Simple local operation | One documented command starts durable chief + local runner with no GCP and no paid planner key; live process IDs and API health recorded. |
| H2 | Editable ordered plans | Import a human-authored Markdown plan; edit a queued item and append/approve an additional item during execution through the CLI. Actual dispatched instructions contain the amendment. |
| H3 | Useful CLI visibility | One command shows plan progress, current item/task, backend/model, elapsed time, repair/validation evidence, and actionable wait reasons; watch mode can be stopped without stopping work. |
| H4 | Strict sequencing | At most one plan item executes at once; each item is reviewed and landed before the next starts; later agents observe earlier committed behavior. Record task timestamps, branches, and landed commits. |
| H5 | Independent implementation | All simulator implementation commits originate from Hive coding tasks. Observer writes briefs and Hive fixes, not simulator implementation. Record every manual intervention and its reason. |
| H6 | Review and executable validation | Every landed item has a fresh review session and a successful runner-executed check tied to its pushed SHA. A failing/rejected attempt cannot land. Record substantive findings and repair attempts, not merely ACCEPT markers. |
| H7 | Strong model access | Successfully execute real work with Codex gpt-6-astra and both Claude Fable and Opus. Verify current exact model IDs from installed/provider metadata. Report actual model identities and results. |
| H8 | Correct quota scope and fallback | Respect shared account limits and model-specific windows separately. When preferred capacity is unavailable, progress through configured alternatives to free Muse/OpenCode. A provider switch preserves code and starts a compatible fresh session. |
| H9 | Honest fallback experiment | Capture live quota telemetry. If natural exhaustion does not occur, use a clearly labeled controlled exhaustion drill on isolated experiment state; never claim that an injected limit was a real exhausted subscription. Do not deliberately burn quota to reach a limit. |
| H10 | Recovery | Perform a controlled local stop/restart while work is unfinished; preserve projects, plan, branch and edits, then complete the interrupted stage without duplicate landing or manual code recovery. |
| H11 | Steady progress and bounded failure | Measure queue-to-start latency, build/review duration, completion throughput, wait time by cause, automatic retries, and observer interventions. No unexplained idle period longer than two minutes when eligible work/capacity exist; provider latency is reported separately. Repairs remain bounded and persistent failures become visible. |
| H12 | Spending policy | Use subscription authentication and explicitly free OpenCode model; no paid API fallback or usage-reset credits. Record configured grants, actual providers/models, and reported spend with its known limitations. |
| P1 | Engineering behavior | Independently check sourced reference cases and dimensional units; positive/plausible cycle time, correct cooling/thickness and clamp/pressure relationships. Distinguish validated approximations from uncalibrated heuristics. |
| P2 | Production conservation | Material inputs equal accepted parts + rejects + runners/scrap + remaining inventory within documented numerical tolerance. No negative inventory, impossible parallel use of a machine/tool, or output while a resource is unavailable. |
| P3 | Reproducible scenarios | Identical seed/configuration yields identical event/result history; save/reload continuation matches uninterrupted simulation. Different timestep/UI speed must not change simulated outcomes. |
| P4 | Planning and scaling usefulness | Demonstrate at least three scenarios (baseline, bottleneck/constraint, scaled capacity) and explain throughput, utilization, cost, scrap, and order completion changes against independently calculated expectations. |
| P5 | Interactive product | Actually run and use the browser UI: inspect a line, alter meaningful process/planning inputs, run/pause/speed simulation, inspect metrics and compare scenarios. Capture and inspect screenshots; no broken core flow or console errors. |
| P6 | Extensibility demonstrated | A later Hive task adds a meaningful process/resource/scenario feature using the existing model without rewriting the engine; its acceptance checks and UI behavior pass. Record the extension diff. |

## Observation record

The evidence ledger will include timestamps, live process/session identifiers,
project and plan IDs, provider/model choices, task outcomes, validation SHAs,
GitHub landing evidence, screenshots, independently run checks, and interventions.
Each criterion ends as proven, failed, or unproven with a link to concrete evidence.
Completion requires every criterion above; green unit tests are supporting evidence.

## Initial inspection

- Hive baseline: `eeb1278`; working tree contains only the user's pre-existing
  untracked `dsu.py`, `foo.py`, and `txt`, which this experiment does not use.
- Installed CLIs: Codex 0.144.5, Claude Code 2.1.226, OpenCode 1.18.27.
- No listeners on local ports 8000 or 8787 at inspection.
- Codex account telemetry reports 4% of its weekly window used. No reset redeemed.
- Known gaps to fix before/during the run: model-specific Claude windows currently
  cool the whole backend; no ordered project-wide fallback chain; sparse plan CLI
  progress details. These are experiment findings, not product-code interventions.

## Goal-turn classification

This is the first live-experiment goal turn. The preceding turn was an advisory
role audit, with no live simulator run. Current progress begins with the inspected
baseline and these committed criteria; no simulator success is claimed yet.
