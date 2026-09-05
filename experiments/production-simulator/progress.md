# Live experiment progress

The acceptance criteria in `metrics.md` remain the completion contract. Three of
nine items have landed; orders, stochastic behavior, replay, and the dashboard
are still pending.

## Install and evidence

- Private target repository: https://github.com/ikamensh/molding-foundry.
- Seed commit `513522f` contains only the brief, plan, sources, and agent guidance.
  All application code is to be written by Hive tasks.
- Local Hive state: `~/.local/share/hive/experiments/production-simulator`.
- Launch: `uv run python experiments/production-simulator/run_local.py`.
- Observe: `uv run hive --local plan molding-foundry --watch`.
- Full read-only observation: `uv run python experiments/production-simulator/observe.py
  molding-foundry --output <new-evidence-directory>`.
- Live project `97694b92005e`, plan `164e1f61d0c0`, runner `77cc3dbb7de9`.
- Snapshots and model probe summaries are in the state's `evidence/` directory;
  `evidence/observed/` contains the ongoing timeline and generated report.

## Intervention ledger

1. Added ordered project model preferences, separate shared/model-scoped Claude
   windows, compatible session switching, and dispatch explanations. Added CLI
   plan detail/watch and a read-only experiment recorder. These change Hive,
   not the simulator.
2. Imported and approved the eight-item engineering plan through the local CLI.
   Configured Astra builds, Fable reviews, and the ordered fallback chain
   Astra → Fable → Opus → free Muse. All three backends have unlimited session
   grants; included-only is enabled with a zero-dollar API budget.
3. First build `861c728d6913` selected Fable after the Codex probe had failed.
   Fable returned an explicit version error before writing application code:
   the SDK bundled Claude Code 2.1.175; Fable required 2.1.251 or newer. Hive's
   discovery incorrectly displayed the unrelated system CLI version 2.1.226.
   The item parked as `blocked_clarity`, which was unhelpful for a runtime fault.
4. Reproduced that failure with a minimal `hive.agents.run_agent` call. Upgraded
   the locked Claude SDK to 0.2.152 (bundled CLI 2.1.259), added a dependency
   minimum, and made discovery inspect the bundled runtime. The same Fable
   probe then returned `HIVE_FABLE_OK`; Opus returned `HIVE_OPUS_OK`.
5. Traced the failed Codex probe: Kodo supplied its old default gpt-5.5 while
   the user's configuration requested an unsupported reasoning effort for that
   model. An explicit Astra probe revealed the system CLI 0.144.5 was also too
   old for Astra. Upgraded the npm-installed CLI to 0.153.4 and made Hive's
   default/overridable Codex model explicit. The real Astra probe then returned
   `HIVE_ASTRA_OK`. Kodo's generic error classification hid the provider error
   behind unrelated MCP authentication warnings; intervention seven fixed that
   error transport.

6. Restarted the idle local install after the runtime/retry fixes. Codex's real
   runner probe `fc3ddc687d5d` passed, and retried the first item through the CLI.
   Task `4249fc9f39ee` began on Astra. While it was running, edited item
   `3bc12caa169d` to require an explicit diffusivity-unit regression and appended
   the production-contract challenge from `game-challenge.md`. New item
   `3533073c86df` is queued ninth. `evidence/live-plan-amendment.json` records
   before/after state and CLI output. Intervention ten verified that item two
   received the amendment, and intervention fourteen checks its behavior.
7. Fixed upstream Kodo's Codex transport so structured quota/model errors survive
   partial replies and unrelated stderr login warnings, including `turn.failed`
   events. All 148 session tests passed. Hive pins commit `f7fe4d8` on Kodo's
   `codex/hive-provider-errors` branch until a release includes it; no PyPI
   publication was performed. Hive's real-process regression verifies quota
   text and reset hints reach the capacity classifier unchanged.
8. Stopped the CLI watch with SIGINT and verified the active build continued.
   Then performed the planned interruption drill at the service level: stopped
   chief 82735 while Astra had 22 files, including uncommitted application code.
   Every recorded file hash and branch matched after shutdown; the entire old
   process tree exited. On restart, Hive terminalized `4249fc9f39ee` as interrupted
   and automatically dispatched successor `ba6b626145fe` on Astra with the same
   branch/runner and `preserve_checkout=True`. No code recovery or manual task
   retry was needed. Intervention ten verified the final reviewed landing.
9. Added and tested OpenCode intake, automatic testing, planner/triage adapter,
   and accurate setup choices. The real full-tool Muse planner smoke initially
   repeated its draft: the prompt said unchanged initial state meant an action
   had not happened. Clarified that later tool results supersede that snapshot;
   the bounded live regression then produced one draft and stopped in round two.
   Included-only/free-provider admission subsequently passed integration checks
   through the API, scheduler, direct planner invocation, and todo triage.
10. The resumed Astra build pushed `cc59891`. Fable independently reviewed it,
    reproduced the clean setup/check flow and six invalid-input CLI cases, and
    fixed a missing inventory count in the CLI summary. Hive ran `make check`
    successfully against reviewed SHA `b134518863f1b6193b321b89609173d54d6433fe`
    before landing item one. Only then did task `c3d6c9ac7195` start item two;
    its instructions contain the queued `1e-6` diffusivity amendment. Changed
    the configured reviewer to Opus for that next increment.
11. Independent observer checkout of the pushed build passed `make setup`,
    `make check` (82 tests), the documented validation CLI, and `make dev` on an
    alternate port with a real `GET /scenario`. The temporary service was then
    stopped. The observer made no simulator code changes. This proves the first
    contract increment, not physical simulation or dashboard quality.
12. Real Muse worker/reviewer tests exposed a second directory-selection bug:
    setting subprocess `cwd` alone still let OpenCode use the caller's Hive
    checkout. Preserved the stray smoke artifacts as evidence, then removed
    only those files. Kodo now passes the explicit `--dir`; Hive pins `a62ee9b`.
    Repeating the test produced code and 13 passing tests in the requested
    throwaway repository, then a fresh Muse reviewer accepted it. An independent
    exhaustive check covered 9,841 inputs and confirmed the caller was untouched.
    The planner has the same directory pin and passed a normal goal invocation
    with all seven tools, producing one draft in two rounds (16.1 seconds).
13. Drained item two's Astra build and restarted the idle chief from `a04833f`
    with the free Muse planner enabled. Resumed immediately and verified Opus
    review task `b56a45769f83` started. Evidence is in
    `evidence/maintenance-muse-runtime.json`. The startup banner incorrectly asked
    for an API key despite the working CLI provider; fixed that wording with four
    CLI launch regressions, and all 48 CLI tests passed.
14. Astra pushed item two at `10c351e98eeb37a40930c42c70bd64f95c84b44a`.
    A separate clean clone passed `make setup` and `make check` (133 tests).
    Thirteen independent CLI scenarios and real HTTP inspection verified the
    thermal, overlap, clamp, material-demand, and incompatible-resource cases.
    The live amendment's 0.1 mm²/s and 1e-7 m²/s inputs produce equal predictions;
    inspection displays both source units and SI values. No observer edits were
    made to the simulator. These checks precede Opus's final review; the reviewed
    SHA and landing still need confirmation. Evidence: `evidence/item2-independent/`.
15. Opus completed the second review, independently recomputed the physics and
    checked invalid-domain boundaries, then removed an unreachable guard and
    clarified a loop variable. Hive ran the 133-test gate against reviewed SHA
    `880eef8944dd69a21106ed3cb66ea899f143acf2` and landed the increment. Item three
    is pending as task `84da504ae40f`; workspace dispatch is paused intentionally
    while the auxiliary-model safeguard and controlled fallback drill are
    completed. Evidence: `evidence/second-landing.json` and
    `evidence/sequence-through-item2.json`. The sequence audit verifies fresh
    review sessions, successful checks at both reviewed SHAs, review after build,
    the next item starting after the previous landing, and no overlapping work
    on different plan items.
16. Resumed item three on Astra while the free-worker configuration safeguard
    is completed, so that independent Hive fixes do not hold up available
    strong-model work. The controlled Muse dispatch drill moves to the next
    suitable task boundary. This was an observer-imposed maintenance wait, not
    a capacity failure; `evidence/resume-during-safeguard-work.json` records the
    transition. The prepared drill has not changed live quota state.
17. Removed the obsolete quota-triggered planner wakeup: deterministic dispatch
    owns provider fallback, so a capacity wait must not prompt plan amendments.
    Fixed the fleet-pause explanation in project/CLI output. A separate API-to-
    restart regression caught the next-goal gate: human requests now persist as
    `pending_iteration_goal`, produce one draft after restart, and are consumed
    when that draft is created. Completed goals cannot trigger unsolicited plans.
18. Fixed explicit CLI-version and configuration refusals. They now create an
    actionable runtime-repair todo and an immutable successor on the same branch,
    allowing another configured provider to continue. The incompatible CLI stays
    unavailable until the exact failed model probes successfully; a default-model
    probe cannot clear it. No quota exhaustion is inferred from a runtime fault.
19. Completed shared OpenCode model isolation in Kodo `4040284`, pinned by Hive
    `e386f96`: private configuration discovery, one permitted provider/model,
    pinned native agents and auxiliaries, fixed titles, and a check of resolved
    settings before model execution. Real hostile personal/managed settings were
    excluded or rejected before a paid model call. Muse built and tested code in
    35.1 seconds; a fresh reviewer accepted it in 54.5 seconds, and a native
    subagent's metadata confirms the same free model. Nested repository
    instructions were delivered. Preflight and native-tool children now stop on
    cancellation; structured errors retain HTTP status. Planner parsing selects
    the final response without joining preceding commentary into its JSON. The
    real normal-goal planner passed in 23.8 seconds and three rounds, producing
    one draft and no execution tasks. Evidence: `evidence/muse-isolation/`.
20. Astra pushed item three at `213e65fa0ec2bf42e062d7aec84b3faabe7d3d4e`.
    Opus independently fuzzed pacing and multi-press conservation, accepted it
    without edits, and Hive ran the 185-test gate before landing it in `50c28f0`.
    A separate clean clone passed 17 external checks across 13 CLI fixtures and
    four pacing comparisons: the one-hour 480-part case, starvation/deliveries,
    per-event material conservation including WIP, final-horizon boundaries, and
    canonical trace equality. The reviewed SHA and main have identical trees.
    Evidence: `evidence/third-landing.json` and `evidence/item3-independent/`.
21. Full Hive validation at `e386f96`: 661 tests passed, one skipped (119.21s),
    lint passed, and wheel/source builds passed in a separate acceptance
    environment. Stopped the idle chief, updated its environment to the tested
    dependency, and applied a **300-second synthetic** scoped capacity drill.
    Actual provider snapshots were preserved. Restarted chief 33865, verified
    the truthful paused CLI state and free-planner banner, then resumed. Real
    item-four task `5cfa44645981` selected free Muse at `1788644131.147882` after
    skipping Astra, Fable, and Opus on `hive/plan-8b390602`.
    This proves live availability-based fallback at a task boundary, not a
    naturally exhausted subscription or an interrupted cross-provider attempt.
    The task's eventual reviewed landing remains to be observed.
    Evidence: `evidence/capacity-h8h9/` and `evidence/verified-runtime-rollout.json`.
22. The synthetic windows expired naturally without changing real exhaustion
    history. Muse remained on the selected free model and stopped 4.57 seconds
    after expiry, but item four did not finish. OpenCode rejected a native shell
    call involving a temporary file in its noninteractive permission path, then
    exited zero without a final response. Kodo treated the last commentary as
    success. Hive's report-only repair offered only fixed/blocked, so the model
    marked the item blocked solely because the report had been missing. This
    was an integration failure, not evidence of a context limit or a missing
    owner decision. Two simulator source files contain partial implementation;
    their hashes and the immutable task report are recorded before recovery.
    Evidence: `evidence/muse-item4-diagnosis/`,
    `evidence/muse-item4-before-recovery.json`, and
    `evidence/capacity-h8h9/independent-audit.md`.
23. A real Git regression showed manual plan retry discarded unpushed commits,
    staged changes, dirty files, and untracked files. Hive `3d71c51` now derives
    continuation from durable attempt history and preserves the branch and
    runner, with a fresh session carrying the amended item document and prior
    report. All 55 relevant plan/runner tests pass. Deployment and live recovery
    await the coordinated unfinished-result and OpenCode permission fixes; the
    partial simulator checkout has not been reset or edited by the observer.
24. Completed the coordinated fixes in Hive `e280306`/`2cd3484` and Kodo
    `5ad352a`. Unfinished builds and reviews now have an explicit result outcome,
    remaining-work evidence, and at most two immutable continuations in the
    same stage. Invalid reports cannot land through a legacy ACCEPT marker;
    genuine blocked builds require a specific owner question. OpenCode now
    records missing terminal completion, retains native refusal details, and
    permits ordinary system temporary files for isolated workers. Planner
    tools and unknown subagents remain restricted. The exact failed trace
    replay passes, a real Muse temporary-file operation passed in 13.94 seconds,
    and all 165 Kodo session tests passed. Hive passed 671 tests (one skipped),
    lint, and wheel/source builds. These commits are pushed.
25. Paused the idle install, stopped chief 33865, verified the runtime pin, and
    started chief 53095 from `2cd3484`. Official CLI retry created
    `67cf3ea20d57`. Before resuming, both partial Muse file hashes matched their
    baseline; the successor retained the branch and original runner, with an
    empty fresh session. All historical task fields remained unchanged. After
    resume, the new task selected Astra at `1788645854.707626`, confirming return
    to preferred subscription capacity after synthetic expiry. The provider
    switch continues real partial code, but this was an operator retry after
    an integration fix, not automatic recovery from genuine quota exhaustion.
    Evidence: `evidence/incomplete-rollout-{before,after}.json`,
    `evidence/muse-item4-retry-paused.json`, and
    `evidence/manual-retry-review/`.

H1–H3 and recovery of the first increment have live evidence. H4–H6 are established
for items one through three and must continue to hold for every later item. H7
has substantive Astra, Fable, and Opus work. H8–H9 now have a labeled live fallback
dispatch, natural expiry, and return to preferred capacity with preserved partial
work; the recovered increment still needs to land. Full-simulator product criteria
remain open. No spontaneous
subscription exhaustion has been observed or claimed.

## User refinement: Muse in every role

Muse/OpenCode is eligible for intake, planning, building, review, triage, and
automatic testing. The API and setup UI expose eligible backend/model pairs from
the same selector. The planner/triage adapter confines native tools and validates
Hive tool requests before executing them; included-only projects can use it at a
zero-dollar daily budget without a paid API fallback. Live planner and worker/
reviewer tests passed. Fresh review sessions and executable validation remain
required when the same model fills every role. Shared worker/planner configuration
isolation now prevents personal OpenCode title/compaction settings from selecting
paid auxiliary models; conflicting managed settings fail before a model call.
Free availability is temporary according to https://opencode.ai/docs/zen/;
the Contributor Free offering also allows model-training use of prompts and
completions. The selected model remains configurable.
