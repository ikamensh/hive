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

## Cycle 7 — retry a cancellation after an uncertain response

- **Scenario:** Cancel the PDF item in a CSV/PDF plan with a deferral reason;
  repeat the cancellation without a body and retrieve the plan result.
- **Initial observation:** `uv run pytest tests/test_plan_usecase_cycles.py::test_repeated_cancellation_preserves_the_reason_in_the_result -q`
  failed: the repeated POST erased `parked_reason` and changed `updated_at`.
- **Decision/fix:** Cancellation of an already-cancelled item is idempotent.
  Its terminal reason and timestamp remain evidence of the original decision.
- **Verification:** The same regression passes; the result retains the PDF
  deferral reason. The focused plan/edit/result suite passes (recorded below).
- **Self-review:** A terminal-state early return avoids rewriting history and
  adds no extra state or retry mechanism.
