# Live experiment progress

The acceptance criteria in `metrics.md` remain the completion contract. No
simulator implementation or product-quality result is claimed yet.

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
   behind unrelated MCP authentication warnings; preserving that error remains
   a separate issue to fix.

6. Restarted the idle local install after the runtime/retry fixes. Codex's real
   runner probe `fc3ddc687d5d` passed, and retried the first item through the CLI.
   Task `4249fc9f39ee` began on Astra. While it was running, edited item
   `3bc12caa169d` to require an explicit diffusivity-unit regression and appended
   the production-contract challenge from `game-challenge.md`. New item
   `3533073c86df` is queued ninth. `evidence/live-plan-amendment.json` records
   before/after state and CLI output. Dispatched use of the amendment is still
   to be checked when item two starts.

The three successful marker probes prove runtime access only. H7 still requires
substantive simulator work by all three models. H4–H6, H8–H10, and the product
criteria still require live execution evidence.

## User refinement: Muse in every role

Muse/OpenCode should be eligible for intake, planning, building, review, and
automatic testing. Existing worker/reviewer configuration already admits it.
Intake allow-lists and zero-dollar automatic-testing gates are being removed;
the separate planner/triage path needs an OpenCode adapter. Fresh review sessions
and executable validation remain required when the same model fills every role.
Free availability is temporary according to https://opencode.ai/docs/zen/;
the selected model must remain configurable.
