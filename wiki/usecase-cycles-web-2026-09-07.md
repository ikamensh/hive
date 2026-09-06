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
- Independent review follow-up: a delayed answer POST plus question navigation
  reset the detail view's busy state and enabled duplicate submission. The
  CLI subagent reproduced this with a held HTTP response. Pending/error state
  now lives alongside drafts at page level, keyed by item. The new
  `inbox keeps an answer pending` journey passes: returning to the question
  still shows disabled Sending, other drafts stay editable, and only one POST
  is delivered. Viewed `inbox-pending-answer-navigation.png`.

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

## Cycle 18 — Retry a rejected repository/budget settings save

- Scenario: edit a project budget, receive an API rejection, then retry.
- Reproduction: `cd web && npm test -- --grep 'project settings explains'`
  failed because no alert appeared; Vite also reported an unhandled rejection.
- Cause/fix: the form had `try/finally` without error handling. It now displays
  the chief's explanation, retains edits, and clears the error on retry.
- Verification: the journey passed with the exact budget saved on retry and no
  remaining alert. Viewed `project-settings-save-error.png`; the explanation is
  adjacent to the edited field and save control.

## Cycle 19 — Assign and remove an explicit OpenCode allowance

- Scenario: add a three-session OpenCode grant, save, reload, inspect on a
  390px-wide phone, then remove and save the grant.
- Reproduction: `cd web && npm test -- --grep 'grant OpenCode explicitly'`
  first failed because the supported backend had no selector. After adding it,
  the same journey exposed horizontal overflow in the settings header.
- Cause/fix: updated the selector to include the existing backend, allowed the
  mobile header to wrap, and replaced a blank obsolete-font remove icon with a
  visible, accessible Remove button.
- Design check: discussed a backend-catalog API with the API subagent; a new
  endpoint was unnecessary for this bounded parity fix.
- Verification: the journey passed, including saved/reloaded backend and cap,
  no mobile overflow, and removal. Viewed both desktop and mobile
  `project-opencode-allowance` screenshots.

## Cycle 20 — Cap a small experiment at fifty cents per day

- Scenario: save a `$0.50` daily budget and reload the settings page.
- Reproduction: `cd web && npm test -- --grep 'fractional daily budget'`
  failed: the browser's integer-only step validation prevented submission.
- Cause/fix: the API accepts nonnegative finite fractional budgets; the input
  now uses `step="any"` to expose that existing capability without rounding.
- Verification: the same journey passed with a `0.5` payload and reloaded value.
  Viewed `project-fractional-budget.png`; the small budget remains legible.
