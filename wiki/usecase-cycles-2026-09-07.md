# Twenty Hive use-case cycles — 2026-09-07

Completed 20 distinct workflows after the requested one-hour wait. Waiting began
at 2026-09-06 21:37:53 UTC; project inspection began at 22:38:05 UTC. The starting
commit was `70b27dc`. Three subagents exercised CLI, plan/runner, and operator API
flows while the primary agent exercised the browser. No user input was requested.

Each cycle tried a concrete workflow, recorded its initial behavior, fixed a
reproduced deficiency where warranted, and verified the result. Eighteen cycles
produced fixes; two existing recovery/concurrency workflows passed unchanged.

| Cycle | Workflow | Result |
| --- | --- | --- |
| 1 | Read a missing, unreadable, or incorrectly encoded plan/spec | Actionable CLI error without a traceback or partial project |
| 2 | Address a project with an ID-shaped name | Correct name/ID resolution, including archived-ID access |
| 3 | Correct intake while accidentally supplying an approval flag | Conflicting actions rejected before any work starts |
| 4 | Create/configure a project with an invalid budget | Invalid cap rejected before partial CLI mutations |
| 5 | Discover a chief for a version check | Connection failures try the next candidate; offline version remains available |
| 6 | Edit queued work while an earlier item executes | Changed instructions require renewed approval |
| 7 | Repeat a cancellation after an uncertain response | Original cancellation reason and timestamp retained |
| 8 | Import Markdown with goal links and fenced examples | Valid Markdown reaches the correct parser and exports losslessly |
| 9 | Concurrently amend a plan spanning API and documentation repositories | Passed: stale revision rejected, accepted work preserved |
| 10 | Stop review, amend, retry, and deliver only part of a plan | Passed: checkout/revision/evidence history stays consistent |
| 11 | Create, configure, archive, and restore a project | Blank names and invalid budgets rejected; numeric overflow returns valid JSON errors |
| 12 | Answer a clarification from Inbox | Blank input keeps the question open; valid answer retained verbatim |
| 13 | Create and finish a manual operator todo | Invalid requests produce useful errors without unusable Inbox rows |
| 14 | Register and manage a member's subscription | Backend, licensing mode, and workspace ownership validated |
| 15 | Change machine power settings and ownership together | Rejected ownership changes preserve all original machine settings |
| 16 | Draft answers while reading related questions and filtering Inbox | Separate drafts survive navigation and successful submission of another answer |
| 17 | Share a pageable Inbox with another operator | Polling recovers the last available page after remote resolutions |
| 18 | Retry a rejected repository/budget settings save | Server explanation shown and unsaved edits retained |
| 19 | Grant, reload, and remove an OpenCode allowance on desktop/mobile | Supported backend selectable; mobile header fits; remove action visible |
| 20 | Cap a small experiment at fifty cents per day | Fractional budget saved and reloaded without rounding |

The detailed logs contain reproductions, regression commands, observed failures,
design decisions, and verification evidence:

- [CLI cycles 1–5](usecase-cycles-cli-2026-09-07.md)
- [Plan/runner cycles 6–10](usecase-cycles-plans-2026-09-07.md)
- [Operator API cycles 11–15](usecase-cycles-api-2026-09-07.md)
- [Browser cycles 16–20](usecase-cycles-web-2026-09-07.md)

## Verification scope

The backend journeys exercise the real CLI, FastAPI application, persistence,
and runner protocol with isolated stores. External GitHub/provider transports
are scripted; the broader plan suite also runs its local Git artifact demo.
Browser journeys run the real React app and HTTP client in Chromium against a
scripted chief. Changed visual states were captured and inspected at desktop
and mobile sizes. Screenshots are in `/tmp/hive-usecase-cycles/screenshots/`.

This exercise validates local product behavior. It does not establish current
live-provider output quality, production credentials, or deployed behavior.
No deployments or external messages were sent. Pre-existing untracked files
`dsu.py`, `foo.py`, and `txt` were left untouched.

## Review and design

The second pass found and fixed archived-ID resolution, overflow-error
serialization, and a duplicate-submission race when navigating during a pending
answer. Changes use existing validation, item-editing, and browser state
locations. The OpenCode selector fix was discussed with a subagent; a new backend
catalogue endpoint would add unnecessary scope for the missing existing choice.
Concurrent plan amendments and interrupted-review recovery needed no new state
or abstractions. Stable units were committed separately.

## Final checks

- `uv run pytest tests/ -q` — **861 passed** in 134.80 seconds, up from the
  initial 824. Nine pre-existing Starlette TestClient deprecation warnings.
- `cd web && npm test` — **40 passed** in 14.9 seconds, up from the initial 34.
- `cd web && npm run build` — passed, including TypeScript checking. The
  pre-existing warning about a bundle larger than 500 kB remains.
- `git ls-files -z '*.py' | xargs -0 uv run ruff check` — passed.
- `git diff --check` — passed.

All reproduction regressions and independent-review follow-ups are resolved.
The final tree has no outstanding changes from this exercise.
