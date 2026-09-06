# Plan workflow use-case cycles — 2026-09-07

These five cycles use isolated local stores and Hive's real HTTP application.
Runner registration, polling and result reporting use the public protocol;
GitHub publication/landing is replaced at the external transport boundary.
No live services, repositories or agents are changed by these scenarios.

## Cycle 6 — edit queued work while the previous item executes

- **Scenario:** Approve a three-item CSV-export plan, amend the second item's
  filtering constraints, finish the first build/review, then approve the amendment.
- **Initial observation:** `uv run pytest tests/test_plan_usecase_cycles.py -q`
  failed: PATCH left the changed item `queued`, so its new instructions retained
  approval from the old revision. File amendments already require approval again.
- **Decision/fix:** Align item editing with file amendments: changing approved or
  queued instructions returns the item to `proposed`. Identical updates preserve
  the existing approval/revision. Parked-item edits still require explicit retry.
  This preserves the documented requirement that execution uses approved input.
- **Verification:** The public runner protocol completes item one; the queue
  waits with statuses `done/proposed/queued`; approving item two dispatches its
  new constraints and current revision. `uv run pytest
  tests/test_plan_usecase_cycles.py tests/test_plan_edit_atomicity.py
  tests/test_plans.py tests/test_plan_artifacts.py -q` → **54 passed**.
- **Self-review:** Kept the rule at the shared item-edit boundary; no route or
  UI-specific approval logic was added.
