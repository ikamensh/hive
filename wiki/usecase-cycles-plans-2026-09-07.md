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
  deferral reason. `uv run pytest tests/test_plan_usecase_cycles.py
  tests/test_plans.py tests/test_plan_results.py tests/test_plan_edit_atomicity.py -q`
  → **51 passed**.
- **Self-review:** A terminal-state early return avoids rewriting history and
  adds no extra state or retry mechanism.

## Cycle 8 — import a Markdown goal with a design link and fenced examples

- **Scenario:** Submit a documentation plan whose goal links a reference design
  before its two task headings; the first task contains a fenced `##` example.
  Retrieve both the original source and canonical export.
- **Initial observation:** `uv run pytest tests/test_plan_usecase_cycles.py::test_markdown_goal_links_survive_import_and_export -q`
  failed with HTTP 400: the link's opening `[` was treated as a TOML table.
- **Decision/fix:** Format detection recognizes a complete TOML table declaration,
  rather than every line beginning with `[`. Markdown links remain Markdown.
- **Verification:** Submission returns two tasks, the goal retains its link,
  source export is byte-for-byte unchanged, and fenced headings stay inside task
  notes. `uv run pytest tests/test_plan_usecase_cycles.py
  tests/test_plan_document.py tests/test_plan_document_format.py
  tests/test_plan_artifacts.py -q` → **44 passed**.
- **Self-review:** Kept the existing strict parsers and changed only format
  selection; invalid TOML is still rejected rather than silently repaired.

## Cycle 9 — concurrent amendments to an API/documentation plan

- **Scenario:** Import API and documentation items with shared contract text
  and a repository baseline. Approve the API item. Two authors export revision
  one; author A improves documentation, then author B submits a stale API edit.
- **Initial observation:** `uv run pytest tests/test_plan_usecase_cycles.py::test_concurrent_multi_repository_amendment_preserves_accepted_work -q`
  → **1 passed**. The stale amendment receives HTTP 409 and an actionable
  revision-conflict message.
- **Decision:** No production change. The accepted documentation amendment
  preserves API approval, the item repositories, shared contract and baseline.
  The stale edit changes neither current source nor retained revision one.
- **Verification:** The scenario checks original submitted text, canonical
  historical/current exports, resolved default repository, and item approval
  via HTTP. Kept as a regression for the concurrent-author property.
- **Self-review:** The API already centralizes revision checks in submission;
  adding client-side concurrency machinery would duplicate that guarantee.
