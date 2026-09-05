# Evidence through the second landing

This is an interim measurement, not full acceptance. Project `97694b92005e`,
plan `164e1f61d0c0`: **2 of 9 items landed** at the cutoff
**2026-09-05 21:03:49.117 UTC**. Item three's subsequent dispatch is included
only to explain the maintenance wait at that boundary; its build is excluded.

Source snapshots are under
`~/.local/share/hive/experiments/production-simulator/evidence/`.
Task timestamps below come from `second-landing.json`; landing/order assertions
come from `sequence-through-item2.json`. Times are seconds rounded to three
decimals. Queue means persisted task creation to start; run means persisted
start to finish, including tool execution and any reporting delay, not isolated
model inference. The observer polls every five seconds and captures capacity
approximately every minute. It started at 20:14:41 UTC, after the first failed
task; earlier timing comes from persisted task records, not continuous observation.

## Task timing and results

| Item / task | Exact model | Queue | Run | Result / interpretation |
| --- | --- | ---: | ---: | --- |
| 1 / `861c728d6913` | `claude-fable-5-1` | 0.001 | 5.201 | Failed before implementation: bundled Claude runtime too old. |
| 1 / `4249fc9f39ee` | `gpt-6-astra` | 0.001 | 696.877 | Substantial unfinished code; controlled service interruption. Finish is recovery's persisted terminalization, not the exact last inference time. |
| 1 / `ba6b626145fe` | `gpt-6-astra` | 12.566 | 301.604 | Automatic successor preserved checkout; build pushed `cc59891`. |
| 1 review / `8b9f84ee51c7` | `claude-fable-5-1` | 13.527 | 170.771 | Fresh review found/fixed missing inventory count; final validation: 82 tests, lint and format. |
| 2 / `c3d6c9ac7195` | `gpt-6-astra` | 7.649 | 866.303 | Cooling, phase overlap, material demand and machine compatibility; pushed `10c351e`. |
| 2 review / `b56a45769f83` | `claude-opus-5` | 27.562 | 304.244 | Fresh physics/boundary review removed unreachable guard and clarified a variable; final validation: 133 tests, lint and format. Queue overlaps an intentional runtime restart. |

Item one landed at 20:43:41.451 UTC, at reviewed SHA
`b134518863f1b6193b321b89609173d54d6433fe`, 1.613 seconds after its review finished.
Item two started 7.653 seconds later and landed at 21:03:49.117 UTC, at reviewed
SHA `880eef8944dd69a21106ed3cb66ea899f143acf2`, 1.903 seconds after its review finished.
Both runner validation results name the reviewed SHA and exit zero. No tasks on
different items overlapped. These assertions cover the first two items only.

Elapsed time from the first attempted dispatch to the second landing was
**57m 40.377s**. From the first usable Astra dispatch it was **40m 4.625s**.
The six attempts account for 2,344.999 seconds of task lifecycle time and 61.305
seconds of queue time. This is not a stable throughput benchmark: startup repair,
a deliberate interruption, and a maintenance restart occurred during the run.

## Waiting and recovery

Ordinary successful pickup samples are 0.001 seconds (first Astra dispatch),
13.527 seconds (Fable review), and 7.649 seconds (item-two build): median 7.649,
maximum 13.527. The automatic recovery queue was 12.566 seconds. The 27.562-second
Opus queue overlaps maintenance and is reported separately.

The first failed Fable attempt finished **17m 30.549s** before the replacement
Astra task was created. The intervention ledger attributes this to SDK/CLI
diagnosis, upgrades, probes, restart and a manual retry. It is neither ordinary
dispatch latency nor provider quota waiting. One automatic interrupted-task retry
and one manually requested replacement after a runtime fault occurred. There
were zero recorded review rejection/repair cycles through item two.

`recovery-before-stop.json`, `recovery-after-stop.json`,
`recovery-after-restart.json`, and `recovery-processes.json` show:

- The successor started **30.435 seconds** after the pre-stop snapshot,
  or **16.029 seconds** after the post-stop snapshot. These are measured bounds
  around the command, not an exact stop-signal-to-recovery duration.
- All **22 file hashes** and branch `hive/plan-bb5f0df7` matched after shutdown;
  no missing or changed files were recorded.
- Successor `ba6b626145fe` references `4249fc9f39ee`, uses the same runner/branch,
  and sets `preserve_checkout=True`. All eight recorded old process IDs exited.
- The resumed stage subsequently completed and landed once, without manual code
  recovery. The interrupted attempt reports no usage; that does not mean it used
  no tokens.

The continuous observer recorded three HTTP outages: **20.029s** during runtime
repair, **15.881s** during the interruption drill, and **5.019s** during the later
Muse-runtime restart; **40.929s total**. These are sampled API outage intervals,
not total unavailable-worker time. In particular, the 122.830-second difference
between the maintenance before/after snapshots is not chief downtime: review
was already running before the after-snapshot.

Item-three task `84da504ae40f` was created at 21:03:49.117 UTC and started at
21:12:16.412 UTC: **8m 27.294s queued**. The ledger and
`resume-during-safeguard-work.json` identify an explicit fleet maintenance pause
while preparing the OpenCode safeguard/drill. The snapshots do not record the
exact pause/resume command times, so this interval cannot be split into precise
paused time versus resume-to-dispatch latency. Earlier project-only pause fields
and wait text did not faithfully expose fleet pause. That visibility was fixed
later; it cannot retroactively establish exact eligibility during every sample.

## Model substance and capacity

| Model | Successful simulator work through cutoff | Reported tokens, input / output | Reported usage cost |
| --- | --- | ---: | ---: |
| `gpt-6-astra` | Two build stages; one also has an interrupted predecessor | 3,423,248 / 25,903 | $0.00000000 |
| `claude-fable-5-1` | Item-one fresh review, six invalid-input checks and inventory-summary fix | 394 / 8,653 | $1.60279275 |
| `claude-opus-5` | Item-two fresh physics review and two code improvements | 58 / 11,585 | $1.63880050 |
| `opencode/muse-spark-free` | No simulator stage by this cutoff; separate real planner and worker/fresh-review smokes passed | Excluded: different workloads | Excluded |

The three strong models did substantive work, beyond probes. Token counters have
different provider/cache semantics and exclude the interrupted attempt's missing
report. The **$3.24159325** sum is task-reported usage valuation, not evidence of
cash API charges. Codex reporting zero is likewise not proof of zero consumption.
Subscription authentication, grants and included-only/$0 configuration support
the intended spending policy; these counters are not a billing audit.

`capacity-h8h9/natural-before.json` captures natural telemetry at 21:05:54.881 UTC:

| Backend / window | Model scope | Initial captured use → later use | Meaning |
| --- | --- | --- | --- |
| Codex, 10,080-minute window | Shared | 5% → 17% | Payload labels it `session`; duration is weekly. |
| Claude, 300-minute session | Shared | 19% → 37% | Applies across Claude models. |
| Claude, 10,080-minute week | Shared | 6% → 9% | Applies across Claude models. |
| Claude, `weekly_fable` | Fable only | 11% → 18% | A separate model-specific constraint. |
| OpenCode | Unavailable telemetry | No windows returned | A usable probe is not an estimate of remaining free capacity. |

Initial values above are from `initial-live.json`, not the earlier preflight
note's Codex 4%. Account telemetry is shared with other activity and cannot be
attributed exclusively to this experiment. No separate Opus window was returned;
that does not exempt Opus from shared Claude limits. Every recorded cooldown and
last-exhaustion timestamp in this capture is zero/empty. The drill manifest was
**prepared, not applied**. Its selection matrix and integration tests support
scope handling, but do not prove live provider switching or natural exhaustion.

## Acceptance position and remaining measurement gaps

| Criterion | Evidence now | Remaining limit |
| --- | --- | --- |
| H1 | Local chief/managed runner, durable restart and documented launcher | Initial runtime upgrades were necessary; first-run convenience was imperfect. |
| H2 | CLI imported, edited and appended while Astra ran; item two received and implemented the unit amendment | Appended ninth item has not executed. |
| H3 | Live plan/watch output; SIGINT left task running and chief healthy | Earlier fleet-pause wait text was inaccurate; full wait-cause behavior needs later live evidence. |
| H4–H6 | Two sequential, fresh-reviewed, validated landings; independent clean-clone checks | Continue for remaining items. No live rejected-review/repair episode yet. Observer clean-clone checks preceded reviewers' final minor edits; final SHAs have the runner's checks. |
| H7 | Substantive Astra, Fable and Opus stages | Established for this interval, not a claim about future runtime availability. |
| H8–H9 | Natural scoped telemetry and prepared, labeled drill | No live quota-triggered switch, preserved cross-provider checkout/session, or applied drill yet. |
| H10 | Controlled interruption recovered and landed once, 22 files preserved | Establishes this local interruption case, not every crash/failure mode. |
| H11 | Timings, classified maintenance gaps, retry counts and intervention ledger | No observed ordinary pickup exceeds two minutes, but exact eligibility and provider-only latency are not continuously instrumented; bounded repair is tested, not exercised live here. |
| H12 | Intended subscription/free configuration and actual strong-model identities | Usage is not cash billing. OpenCode auxiliary-model isolation was still under repair at cutoff; all-free simulator fallback awaits its verification. |
| P1–P6 | Independent first contract checks and 13 item-two physical/unit scenarios | Full event simulation, conservation, replay, scaling comparisons, browser workflow and extensibility acceptance remain open. |

Separate Muse smokes establish ordinary full-tool planning, raw triage and coding
with a fresh reviewer in throwaway repositories. Intake start/continue and
automatic-testing dispatch have integration coverage; no evidence in this run yet
establishes a complete live Muse intake-finalize or testability/sweep/confirm cycle.
Manual changes and restart reasons are recorded in `progress.md`; no independent
observer simulator implementation edits are recorded. The first two reviewed
landings are progress toward acceptance, not completion of the experiment.
