# Live experiment progress

The acceptance criteria in `metrics.md` remain the completion contract. Two of
nine items have landed; the event simulation and dashboard are still pending.

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

H1–H3 and recovery of the first increment have live evidence. H4–H6 are established
for items one and two and must continue to hold for every later item. H7 has
substantive Astra, Fable, and Opus work. H8–H9 still
need the controlled capacity drill, and all full-simulator product criteria remain
open. No spontaneous subscription exhaustion has been observed or claimed.

## User refinement: Muse in every role

Muse/OpenCode is eligible for intake, planning, building, review, triage, and
automatic testing. The API and setup UI expose eligible backend/model pairs from
the same selector. The planner/triage adapter confines native tools and validates
Hive tool requests before executing them; included-only projects can use it at a
zero-dollar daily budget without a paid API fallback. Live planner and worker/
reviewer tests passed. Fresh review sessions and executable validation remain
required when the same model fills every role. A further worker safeguard is
being completed: personal OpenCode title/compaction agent settings must not cause
auxiliary calls to a paid model. The controlled fallback drill waits for that fix.
Free availability is temporary according to https://opencode.ai/docs/zen/;
the Contributor Free offering also allows model-training use of prompts and
completions. The selected model remains configurable.
