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
  tests/test_api_e2e.py tests/test_users_roles.py -q` passes.
