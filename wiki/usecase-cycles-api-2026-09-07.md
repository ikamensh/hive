# API use-case cycles — 2026-09-07

These are cycles 11–15 of the requested iterative Hive exercise. Each uses the
real FastAPI app and in-memory persistence; no external services or model calls.
The HTTP client does not enter app lifespan, so the supervisor stays under test
control. Input rejection is verified by reading the affected state back through
HTTP. System-generated records may be seeded as initial scenario fixtures.

## Cycle 11 — Create, configure, archive, and restore a project

- Tried: create with a whitespace-only name; create a named project; submit a
  negative budget; archive it; inspect default and archived lists; restore it.
- Initial observation: `POST /api/projects {"name":"   "}` returned 200 and
  persisted `name: ""`; `PATCH ... {"daily_budget_usd":-5}` returned 200 and
  persisted `-5.0`. Archive/list behavior worked, leaving the nameless project
  visible after the named one was archived.
- Fix: creation now uses the same explicit nonempty-name rule as rename; the
  budget request field accepts only finite, nonnegative amounts.
- Evidence: `uv run pytest tests/test_api_operator_workflows.py -q` initially
  failed with `assert 200 == 400`. The regression covers invalid creation,
  atomic rejection of a rename plus invalid budget, valid zero budget, archive,
  explicit archived listing, and restore.
- Verified: `uv run pytest tests/test_api_operator_workflows.py
  tests/test_api_e2e.py tests/test_users_roles.py -q` — 60 passed.

## Cycle 12 — Answer an Inbox clarification, correcting empty input

- Tried: open a system-generated question, submit whitespace as the answer,
  inspect Inbox, submit a real multiline answer, and read project history.
- Initial observation: the blank answer returned 200 with `status: answered`;
  the question disappeared from Inbox, whose question total fell from 1 to 0.
- Fix: reject whitespace-only answers with 400 before any logging, question
  update, or planner wake. Valid answers retain their exact original text.
- Evidence: `uv run pytest tests/test_api_operator_workflows.py -q` initially
  failed at the new blank-answer assertion (`assert 200 == 400`).
- Verified: `uv run pytest tests/test_api_operator_workflows.py
  tests/test_inbox_api.py tests/test_api_e2e.py -k 'question or answer or
  project_setup' -q` — passes; the invalid answer remains open, a valid answer
  clears Inbox, and project history retains the answer.
