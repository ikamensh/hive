# Live experiment progress

The acceptance criteria in `metrics.md` remain the completion contract. Eight of
ten items have landed, including the independently verified quality/checkpoint
repair and the dashboard. Muse's correct scope rejection triggered an automatic
Astra repair; after fixing the Claude transport, fresh Opus review and executable
validation accepted the completed dashboard. Independent browser/API checks pass.
Hive has automatically started scaling and packaging with Astra; the contract
challenge remains queued.

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
26. The recovered Astra build pushed `763aba3`, with 223 tests and a clean setup
    check. Opus review `4462b503679f` independently exercised 400 randomized
    multi-press/tool/material scenarios, verified FIFO/EDD tardiness of 40 s
    versus 15 s, and documented the dispatcher's same-part recipe fallback.
    Hive validated reviewed SHA `99d8addba63535b1f89397135952510003bae572`
    with all 223 tests passing and landed item four in `c27d564`. An independent
    clean checkout of the reviewed SHA passes the 223-test gate and 37 external
    engine groups: 14 order/edit cases, six resource/atomicity regressions, and
    17 prior conservation/horizon/pacing cases. Real HTTP and ten CLI rejection
    probes pass on the identical builder implementation. The review changed
    only README; main contains the reviewed SHA with an identical tree. No
    observer implementation edits were made. Evidence: `evidence/item4-build.json`,
    `evidence/fourth-landing.json`, and `evidence/item4-independent/`.
27. During the running Opus review, explicitly selected free Muse for subsequent
    builds and reviews, aiming to exercise item five entirely on Muse. This is
    a role-selection trial, with existing subscription fallback capacity still
    available; no synthetic limits were applied. Task `5870f8bdb1b9` began item
    five on Muse at `1788647146.181066`. The planner remains free Muse. Restore
    the preferred Astra builder and Opus reviewer after item five's fresh Muse
    review starts. Evidence: `evidence/all-muse-increment-{before,after}.json`.
28. Found and fixed one default-selection inconsistency in Hive `b2983d0`:
    intake hardcoded an older Codex model while workers defaulted to Astra.
    Intake, dispatch, and the worker factory now share the same current default
    and `HIVE_CODEX_MODEL` override. Blank model preferences previously matched
    unrelated model cooldowns conservatively; resolving the concrete model
    avoids that overblocking. Explicit model preferences and grants remain
    effective. All 121 relevant API/worker/capacity/isolation tests pass, and the
    commit is pushed. The running install has not been restarted for this fix;
    its experiment tasks already use explicit models.

29. Free Muse completed item five as task `5870f8bdb1b9` in 812.81 seconds,
    pushing `8f3d174b905a1835c238c1a3882879586eadbd48`. A distinct fresh Muse
    session (`bb52a7061b16`) accepted the same SHA in 74.15 seconds. Hive ran
    `make check` successfully with 232 tests and landed the item. The builder
    session was `ses_f8c527099ffe4pGcqNfvBM2qeB`; the reviewer session was
    `ses_f8c45d5b0ffeHLfnzpUCzxRaiq`. This proves a substantial all-Muse build,
    fresh review, and gated landing, not independent product correctness.
    Restored Astra/Opus after the Muse review was delivered; Astra task
    `8d2591c67f77` started save/replay at `1788648061.718871`.
    Evidence: `evidence/item5-{build,review-start,review-result}.json` and
    `evidence/all-muse-increment-restored.json`.
30. Independent acceptance of the exact item-five SHA passed the 6-kWh energy,
    cost, and public-engine 0.675-OEE references, but found a concrete review
    miss: at saturated rejection probability, rounding residuals are assigned
    to holding pressure even when its contribution is zero. Mass and counts
    conserve; the cause diagnosis is wrong. Added and approved repair item
    `4d94b15be719` through the CLI, placed after the active save/replay item and
    before the dashboard. The queue now contains ten items. The immutable
    accepted build/review remains unchanged; no simulator implementation was
    edited by the observer. The repair brief requires zero-weight exclusion,
    per-batch/aggregate cause agreement, and pacing/save reproducibility.
    Evidence: `evidence/item5-independent/` and
    `evidence/item5-cause-repair-amendment.json`.

31. Tightened the plan-review prompt after the independent review miss: the
    reviewer must exercise a relevant boundary/invariant through a public
    interface, derive the expected result independently of the existing tests,
    and report expected/observed evidence and remaining verification. This is
    a short review obligation, not a guarantee of defect detection. All 35
    plan/validation integration tests pass. The chief caches prompts, so live
    adoption awaits an idle restart; existing task instructions are unchanged.

32. Astra completed save/replay task `8d2591c67f77` in 867.38 seconds and
    pushed `75394c730fb1bd4317a5f95d3978c4bf933b099d`. Fresh Opus review
    `bd9f02beb1a7` accepted the unchanged SHA after 500.89 seconds, independently
    checking chained stochastic checkpoints, live HTTP rollback, unlimited-mode
    resume, and pacing. Hive's gate passed all 308 tests and landed the item in
    `4a670593a0b9ccd889a5e5a0c8792fcc9be97d5e`, with an identical tree.
    Independent checks passed across 32 real processes and 429 HTTP requests,
    including 29 atomic invalid-input cases, exact RNG/state/event continuity,
    future commands, CLI replay/resume, setup/purge WIP, and finished boundaries.
    Loads reconstruct and verify the saved state by replay; this documented
    tradeoff makes load time grow with history. Evidence: `evidence/item6-build.json`,
    `evidence/sixth-landing.json`, and `evidence/item6-independent/`.
33. Independent raw-byte checks found duplicate JSON version keys silently
    accepted by both HTTP and CLI checkpoint loading. The core save/replay
    checks pass, but this input-boundary gap needs repair. Paused new dispatches
    during the running Opus review at `1788648965.064064` to permit an idle
    chief update. After review finished, Hive had prepared pending task
    `8305e2cd2783` for the existing repair. Editing refused until cancellation,
    as the item was already resolving. Inspection found manual retry would
    wrongly preserve a checkout for this never-delivered task, whose branch
    has never been created. Cancelled it through the CLI and amended the repair
    to cover both independent findings; retry waits for the tested Hive fix.
    No running task was interrupted and no simulator code was edited. Evidence:
    `evidence/review-prompt-rollout-paused.json` and
    `evidence/item6-duplicate-key-repair-amendment.json`.

34. Fixed the never-delivered retry case in Hive `ea8cd45`: continuation
    history now considers only tasks delivered to a runner. A canceled pending
    or dispatched-but-unpolled task gets a fresh checkout; an earlier delivered
    attempt still preserves its work when a later undelivered retry is canceled.
    All 76 relevant plan/recovery/real-Git checkout tests pass, with lint clean.
    The fix is committed and pushed. Live retry will follow the idle restart;
    the canceled unrun task remains in history with zero execution time.

35. Stopped idle chief 53095 and started chief 89922 from `4f1187b`,
    loading the shared Codex defaults, independent review prompt, and safe
    never-delivered retry fix. Official CLI retry created `4359cc691d25` with
    `fresh_branch=True`, `preserve_checkout=False`, no session/runner pin,
    and the amended two-finding document. Resumed and verified Astra received
    it at `1788649983.629233`; the real checkout is now `hive/plan-4d94b15b`.
    The canceled task remains never-delivered with zero start time. No active
    task was interrupted by maintenance. The next freshly created review will
    carry the stronger prompt; existing historical instructions are unchanged.
    Evidence: `evidence/review-prompt-rollout-{before-stop,after-retry}.json`
    and `evidence/combined-repair-start.json`.

36. Fixed a confirmed Codex quota-display bug in Hive `7784612`: window
    kind now derives from actual duration rather than assuming the native
    primary slot is a session window. Native-slot severity remains correct.
    Four real-rollout→collector→runner-registration→CLI cases cover weekly-only,
    swapped exhausted slots, and unknown/missing duration. All 84 relevant
    tests, lint, and a wheel build pass. Evidence:
    `evidence/codex-window-label-audit/`.
37. Astra pushed combined repair `b8b461a700e7fd759ac4656560cfcd56fcc13378`
    after 598.80 seconds. Fresh Opus review `c6af1cc847b6` received prompt
    `8017335d`, including the new independent-probe obligation, and reported
    explicit public-interface commands with expected/observed results. Hive
    passed the 415-test gate and landed the unchanged SHA in
    `61f3268b1a316c7e2c568d745873cca944d2d0d2`. Independent acceptance passes
    33 cause boundary fixtures, 72 stochastic/pacing runs, and 57 pre/post
    comparisons preserving every physical field and exact RNG state while
    correcting cause labels. Checkpoint duplicate keys reject through 12 HTTP
    and 12 CLI cases; the 32-process/429-request replay suite remains green.
    Saves now identify engine contract 2; contract-1 checkpoints fail explicitly
    because corrected cause histories differ. The original failing evidence
    and both earlier accepted reviews remain unchanged. Evidence:
    `evidence/combined-repair-{build,landing}.json`,
    `evidence/cause-repair-independent/`, and `evidence/item6-independent/`.
38. The independent raw-request matrix also found duplicate operation/nested
    order/recipe keys accepted by the separate action endpoint. Added a queued
    dashboard amendment through the CLI to use consistent strict JSON decoding
    while preserving typed validation/OpenAPI, actionable errors and full
    rejected-request atomicity. Its dispatched instructions include the clause.
    This third finding remains open; it is separate from the now-verified
    checkpoint repair. Evidence: `evidence/dashboard-action-validation-amendment.json`.
39. Prepared a second controlled fallback drill, then paused during the running
    repair review and waited for its landing. Stopped idle chief 89922, applied
    **300-second synthetic** exact-model cooldowns for Astra/Fable/Opus, and
    started chief 456 from `7784612`. Native provider snapshots and real quota
    history were preserved. Resumed: dashboard task `f8defcefdf25` selected free
    Muse at `1788651117.174665`, explicitly skipping the three cooled scopes.
    The CLI now correctly displays Codex's seven-day window as weekly. The
    drill tests full-task fallback after the native completion fixes; dashboard
    completion, cooldown expiry and later preferred-model return remain pending.
    Manifest `bc331a30cb86` expires at `1788651372.1330812` without a manual reset.
    Evidence: `evidence/capacity-h8h9-dashboard/` and
    `evidence/dashboard-drill-pause.json`.

40. Muse task `f8defcefdf25` finished in 158.49 seconds, but implemented only
    the strict action-JSON amendment at `ffae33967a0d95ad352771514749b8538ed2acc7`.
    No frontend, browser tests, or frontend gate existed; the builder incorrectly
    reported the whole item fixed. Fresh Muse review `b5057b683158` correctly
    rejected the missing scope after 81.35 seconds despite 425 passing Python
    tests, with a concrete correction brief. The rejected dashboard head did
    not land. The action fix independently passes all raw duplicate-key and
    continuation checks, but remains provisional until an accepted descendant
    lands. Hive automatically created repair `ca9a2cb0d328` and selected Astra at
    `1788651372.583348`, 0.45 seconds after the synthetic deadline. It retained
    the branch and exact committed Muse head, used `preserve_checkout=True`,
    and started fresh native Astra session `01a073ee-0344-71a0-a726-85477de86b3a`.
    No operator retry or scope amendment was needed for this correction. This
    proves the substantive review gate and automatic return to preferred
    capacity; full dashboard completion still awaits the repair. Evidence:
    `evidence/dashboard-rejection-and-repair.json`,
    `evidence/capacity-h8h9-dashboard/automatic-astra-{repair-checkout,session-start}.json`,
    `evidence/ui-independent/runs/ffae33967a0d95ad352771514749b8538ed2acc7/`,
    and `evidence/dashboard-automatic-repair-cli.txt`.

41. The live rejected dashboard exposed a CLI ambiguity: a green validation
    result appeared beside a truncated review summary without an explicit
    verdict. Hive `5008032` now renders the latest finished review decision
    independently from the validation result. Rejected/failed/incomplete
    reviews cannot read as accepted; an accepted structured report remains
    authoritative over a retained earlier transport warning. All 101 relevant
    CLI/plan tests, lint and a wheel build pass. A read-only live render confirms
    `last review: REJECT` and `last validation: PASS` appear together. This
    client-only change requires no chief interruption. Evidence:
    `evidence/plan-review-visibility/`.

42. Automatic Astra repair `ca9a2cb0d328` completed in 1673.03 seconds and pushed
    the full dashboard at `a434f7b44603ba83408fe69f889dedbba2e5e654`, preserving
    Muse's API fix. A separate exact-SHA clone passes `make setup`, `make check`
    (434 Python tests, four real-browser tests, frontend formatting/types/build)
    and the independent raw-JSON/API/continuation suite. Root used the documented
    production server at port 64610: step, advance, invalid input, recipe edits
    preserving captured shots, pending demand, scheduled cooling, run/pause/speed,
    real checkpoint download/reset/upload, scenario selection, metrics and events.
    Desktop and narrow screenshots were seen. The downloaded baseline save has
    exactly the same physical state, RNG and 485 events as an uninterrupted run;
    the paced cooling case likewise matches all 383 events and the independently
    calculated 376-part output. Browser console errors/warnings were absent.
    Fresh Opus review `0512e87fbdeb` started at `1788653054.0219588`, then failed
    after 142.07 seconds on `CLIJSONDecodeError: JSON message exceeded maximum
    buffer size of 1048576 bytes`. Its verdict is NONE and it has no validation.
    The dashboard remains unlanded; product checks do not substitute for review.
    Evidence: `evidence/ui-independent/runs/a434f7b44603ba83408fe69f889dedbba2e5e654/`,
    `evidence/item6-independent/runs/a434f7b44603ba83408fe69f889dedbba2e5e654/`,
    and `evidence/dashboard-review-buffer-failure/`.

43. Hive `e2c81fa` classifies the exact Claude SDK buffer diagnostic as a runtime
    failure. Future occurrences preserve the failed review, queue a fresh review
    on the retained checkout using another configured provider, and create an
    actionable runtime repair. They do not fabricate quota exhaustion or treat
    a transport failure as a product rejection. Both Astra and free Muse fallback
    cases pass integration regressions; 71 relevant tests, lint and wheel build
    pass. A separate Kodo transport fix and official retry of the already parked
    historical attempt are pending; no old task record was rewritten.

44. Kodo `fa38b00` gives Claude sessions a bounded 16 MiB SDK message buffer,
    configurable and retained by fresh clones. A real SDK/subprocess regression
    exercises a 2 MiB image-bearing tool result, a second query and visible
    failure when an explicitly smaller cap is exceeded. All 170 session tests
    pass; five new transport cases also pass on the SDK minimum. A real
    subscription Opus smoke read an image whose serialized SDK message is
    **1,258,037 bytes**, above the old 1,048,576-byte limit, and finished normally
    in 19 seconds. Its earlier 1,035,765-byte smoke is retained and explicitly
    does not prove crossing the old limit. Only type/size metadata and final
    output were captured. Hive `e95c496` pins the tested Kodo revision.
    Hive `c30b1c7` also makes an official manual retry resume a failed unfinished
    review, with fresh session and preserved checkout; real REJECT or a nonzero
    gate still returns to the builder. FileStore/restart/cancellation regressions
    pass, including missing validation evidence requiring renewed review.
    Paused at `1788654054.2819462`, gracefully stopped idle chief 456, installed
    the pin, and passed all **701 Hive tests (one skip, 109.56 seconds)** plus
    lint and wheel/sdist build. Chief 37358 runs the updated source. Official
    `plan-retry 9360ea649d57` created review `1e5e313cfe2c`, retaining the original
    runner/branch and `make check`, with a blank session and immutable failed
    predecessor. After the native large-message proof, resumed the workspace:
    Opus review started at `1788654376.43871`. This is an operator retry after
    an integration fix, not a retroactive claim of automatic recovery.
    Evidence: `evidence/dashboard-review-buffer-failure/` including native smoke,
    full-suite log, `rollout-and-official-review-retry.json` and `resumed-review.json`.

45. The retried Opus review `1e5e313cfe2c` accepted at `1788655011.960947`
    after 635.52 seconds. Native session `fdad0f88-8e21-4fdf-af1d-fb9ffcbfd916`
    is distinct from the failed review, and its completion-time API handle now
    provides the direct join. It independently probed duplicate-key rejection
    and unchanged continuation, inspected screenshots, and fixed the CLI error
    to name `--web-dir` when the frontend directory has no index. Its checked
    SHA `1ea8136e7ec8afd877bf0506ef19d5b047ef18e9` passes 435 Python and four
    browser tests. The only review delta is that CLI diagnostic and its test;
    frontend assets match the independently exercised dashboard. Main merge
    `ea67466b7893ac6a71b575d59960a171451ccfe4` has the identical reviewed tree.
    The earlier rejected Muse head remains an ancestor containing the preserved
    API fix, not an independently accepted dashboard landing. Hive automatically
    began packaging/scaling build `d040370c588a` with Astra at
    `1788655022.281704`, after dashboard review and landing. Evidence:
    `evidence/dashboard-landing-and-packaging-start.json` and
    `evidence/dashboard-review-buffer-failure/independent-*.json`.

46. Astra packaging/scaling build `d040370c588a` finished at
    `1788656089.952081` (1067.67 seconds), pushing
    `3245eb96d7dcda73b6d170ca33c86dfeafb0b48c`. The workspace was intentionally
    paused/draining at `1788655795.001405`; the build completed without interruption
    and fresh review `d016a753c9d9` waited pending. Hive `0010ab7` now fetches the
    remote default HEAD before review, records its exact SHA in a task artifact,
    and supplies canonical diff/log commands without changing the working branch.
    Hive `bd8e13e` prepends the actual checkout path and task-owned PID cleanup
    instructions at delivery, including for previously queued instructions. This
    follows the dashboard review's broad `pkill -f "moldsim serve"`, which also
    stopped the independent test server. The new contract is prompt guidance,
    not OS isolation. A real Opus runner smoke stopped only its own server and
    verified the independent same-named sentinel still listened. Its collector
    then looked for a file at the wrong path; native public tool evidence preserves
    the successful cleanup and the collector limitation explicitly.
    Gracefully restarted idle chief 37358 as 64399 on `bd8e13e`, waited for the
    independent browser gate to release its fixed port, and resumed. Packaging
    review started at `1788657020.729727`; its fresh native session received the
    recorded base `ea67466b7893ac6a71b575d59960a171451ccfe4`, current context,
    and unchanged cached review instructions. The first native Bash command
    uses that base. Nine independent delivery checks pass. All **708 Hive tests
    pass (one skip, 125.94 seconds)**. Packaging also passes a clean independent
    `make setup`/`make check` with 483 Python and eight browser tests, plus all 15
    prewritten independent shipping/scaling oracles. Review and landing remain
    pending. Evidence: `evidence/review-baseline-audit/`,
    `evidence/review-process-cleanup/`, and
    `evidence/scaling-packaging-independent/runs/3245eb96d7dcda73b6d170ca33c86dfeafb0b48c/`.

47. Packaging review `d016a753c9d9` accepted at `1788657481.838233`
    after 461.11 seconds. It independently exercised 5 s packaging, the exact
    minimum batch buffer and four presses with rejects/failures. Its first
    `make check` found a browser-test race against the ten-seed calculation;
    reviewer commit `78e9af3b1327459a05cbc71d0f265a5dc672209c` adds bounded
    waits and verifies the rerun actually starts. Only browser tests changed.
    The runner gate and a separate clean final-SHA setup/gate both pass 483
    Python and eight browser tests. Main
    `c543a792060db1ff8901c5b25e4a8130816dc9ec` has the identical reviewed tree
    `5071185e3ec95ef2a6d3fdcbb04247f80a697910`. The baseline artifact is now
    retrieved from the chief and joined to the exact completed native session.
    Independent shipping checks cover 15 prewritten cases, 336 invariant
    snapshots, and 55 deterministic/stochastic CLI samples with separate
    interval arithmetic. The operation extension keeps the existing event
    clock and shares a 45-line reservation/token seam across molding and
    packaging; the engine remains a large explicit domain coordinator.
    Separate actual HTTP suites verify strict input and complete active,
    blocked, repair, RNG and checkpoint continuation state. Root operated the
    real browser in headless Chromium because the Mac was locked, inspected
    desktop/narrow screenshots, and exercised comparison, run/pause, and actual
    checkpoint download/reset/upload. The downloaded 47 s checkpoint resumes
    through CLI to a save exactly equal to the browser's 3600 s download,
    including its journal: 366 molded good parts and 358 shipments. Keyboard
    scrolling exposes all narrow-table columns; browser errors are absent.
    All production bytes are unchanged by review, so these checks bind to the
    final reviewed head. Hive automatically started game build `a5b246432997`
    with Astra at `1788657486.149096`, 4.31 seconds after review completion and
    2.31 seconds after recorded landing. Nine of ten items are landed. Evidence:
    `evidence/packaging-landing-and-game-start.json`,
    `evidence/scaling-packaging-independent/runs/78e9af3b1327459a05cbc71d0f265a5dc672209c/`,
    builder run `manual-ui/`, and `evidence/packaging-api-independent/`.

H1–H3 and recovery of the first increment have live evidence. H4–H6 are established
for all nine landed increments and must continue to hold for every later item. H7
has substantive Astra, Fable, and Opus work. H8–H9 now have a labeled live fallback
dispatch, natural expiry, and return to preferred capacity with preserved partial
work and the recovered increment's reviewed landing. Full-simulator product criteria
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
