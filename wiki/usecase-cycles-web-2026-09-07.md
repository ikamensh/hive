# Browser use-case cycles — 2026-09-07

These cycles exercise the React application and HTTP client in Chromium with a
scripted remote chief. They test browser behavior, not real agents or deployment.
The initial browser baseline was 34 passing tests.

## Cycle 16 — Compose answers while reading related inbox questions

- Scenario: draft two different answers, switch between questions, filter the
  list to zero matches, clear the filter, and submit the second answer.
- Reproduction: `cd web && npm test -- --grep 'separate answer drafts'` failed:
  returning to the first question gave an empty textarea instead of its draft.
- Cause/fix: the detail view owned draft state and was remounted per selection.
  Drafts now belong to the inbox page and are keyed by item. Only a successfully
  resolved item's draft is cleared; the textarea is disabled during submission.
- Verification: the same browser journey passed, including correct question ID
  and answer payload and preservation of the other draft after submission.
- Visual check: viewed `/tmp/hive-usecase-cycles/screenshots/inbox-preserved-draft.png`.
- Scope: drafts survive selection/filter/pagination within the current inbox
  visit; they are not persisted across page reloads.

## Cycle 17 — Share a pageable inbox with another operator

- Scenario: open item 13 on page two; another operator resolves that item, then
  empties the inbox, then a new question arrives.
- Reproduction: `cd web && npm test -- --grep 'another operator resolves'`
  failed after polling: the page stayed at the now-invalid offset instead of
  displaying the 12 remaining items.
- Cause/fix: page recovery ran only after local actions. Fresh observations now
  clamp the offset to the last available page, including zero remaining items.
  This replaces the special case for local resolution.
- Verification: the same browser journey passed through all three remote state
  changes (12.4 seconds). Viewed `inbox-page-recovery.png` under the screenshot
  directory above; remaining work and pagination are visible.
