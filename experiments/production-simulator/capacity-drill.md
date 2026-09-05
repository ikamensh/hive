# Controlled capacity drill: H8 and H9

This is a labeled experiment on the isolated local simulator install. It does
not burn quota, redeem resets, change provider accounts, or report synthetic
limits as exhausted subscriptions. Natural usage windows are captured before
the drill and continue updating throughout it.

The API supports resource enable/disable and runner-reported usage, but no
isolated test override. Spoofing runner heartbeats would race real telemetry.
Local FileStore also caches documents in the chief, so editing its JSON files
while the chief is running is unsafe. This script therefore applies changes
only while the chief is stopped, the workspace is paused, and all work drained.
It verifies the isolated store path and fences other chiefs with the leader lease.

## Procedure

1. During an independent review, pause the workspace through the normal CLI/API.
   Let the running task finish normally. Verify no task or probe is running.
2. Prepare the evidence while the chief is still available. This is a dry run:

   ```bash
   uv run python experiments/production-simulator/capacity_drill.py prepare 97694b92005e \
     --output ~/.local/share/hive/experiments/production-simulator/evidence/capacity-h8h9
   ```

   Inspect `natural-before.json` and `selection-matrix.json`. The latter runs
   the real dispatcher against six in-memory scenarios, retaining capacity and
   grants but omitting active work to represent the next idle boundary. It
   distinguishes individual Claude model windows from a shared Claude window.
   No synthetic task reaches a runner. Unavailable natural capacity can affect
   the matrix; inspect actual statuses, not only its scenario names.
3. Stop the idle local chief and its managed runner normally. Then apply:

   ```bash
   uv run python experiments/production-simulator/capacity_drill.py apply \
     ~/.local/share/hive/experiments/production-simulator/evidence/capacity-h8h9/manifest.json \
     --duration 300
   ```

   `--duration` is seconds, between 60 and 7200; default 3600. Five minutes
   allows a normal restart and dispatch. The manifest is written before any
   capacity changes, so an interrupted apply can be retried or restored.
4. Restart using `run_local.py`, leaving the persisted workspace pause in place.
   Inspect the cooldowns, then resume through the normal CLI/API. The next real
   build should select free Muse and record why earlier candidates were skipped.
   Capture its actual attempt, model, dispatch reason, branch and eventual result:

   ```bash
   uv run python experiments/production-simulator/capacity_drill.py capture \
     ~/.local/share/hive/experiments/production-simulator/evidence/capacity-h8h9/manifest.json
   ```

5. Run `capture` again after the deadline. Its `drill` section reports expiry
   and whether any injected entries still actively block capacity. Expiry does
   not change the model of an already-running Muse task; subsequent attempts
   can choose subscription models again. No second maintenance restart is
   required: expired map entries are ignored by availability and limits views.

For optional cleanup at a later idle boundary, pause, drain and stop normally,
then use `restore <manifest.json>`. Restoration changes only still-owned exact
model-cooldown entries. Newer telemetry, counters, unrelated limits and changed
quota deadlines survive. A conflict is retained and reported with exit status 2;
the script never forces an old value over it. Restart and capture natural state.

## What the evidence establishes

The live mutation adds finite `model_cooldowns` for the exact configured Codex
and Claude model IDs preceding free OpenCode. Existing longer real limits are
never shortened. Ordinary provider heartbeats replace usage snapshots but do
not erase these scoped cooldowns; successful startup probes do not erase them
either. Because shared backend availability remains usable, the injected
scopes do not themselves cause startup re-probes.

Synthetic audit rows use `source=synthetic_h8h9` and `kind=synthetic_capacity`.
They are deliberately excluded from real exhaustion counters and learned quota
budgets. The evidence manifest is the attribution for these synthetic limits;
the normal capacity display exposes the model cooldown deadlines.

This proves availability-based fallback to real Muse work when the preferred
models are deliberately unavailable. It does not establish natural subscription
exhaustion, or by itself prove resumption of dirty work on a different provider.
Keep those claims separate from the earlier retry/recovery integration checks.
