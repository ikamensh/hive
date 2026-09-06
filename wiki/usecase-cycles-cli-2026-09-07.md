# CLI use-case cycles — 2026-09-07

These are cycles 1–5 of the iterative Hive exercise. Commands run against the
real CLI entrypoint or its public `run(args, client)` interface with the real
FastAPI application and an isolated in-memory store. No external service calls,
deployments, live project mutations, or user inputs were needed.

## Cycle 1 — Open a local plan/spec with a bad filename or encoding

- Scenario: validate a plan before handing it over; then check that project
  creation and plan import handle the same local input errors consistently.
- Initial reproduction: `uv run hive plan-check
  /tmp/hive-usecase-missing-plan-2026-09-07.toml` exited nonzero with a Python
  `FileNotFoundError` traceback. The command showed implementation frames
  instead of just the actionable file error.
- Regression first: `uv run pytest -q tests/test_cli_usecases.py` failed all
  10 cases covering missing files, directory paths, invalid UTF-8, three input
  commands, and a real CLI subprocess. API reads also assert no project exists
  after the rejected input.
- Fix: share the UTF-8 input reader between `plan-check`, `plan-import`, and
  `new --spec`. Filesystem/encoding failures retain the cause and filename in
  a nonzero CLI exit; original line endings remain intact.
- Verified: the original shell command now prints one `Cannot read input …`
  error without a traceback. The regression suite passes.
- Self-review: the shared reader replaces three implementations; it catches
  only failures to read/decode input, preserving unexpected exceptions.

## Cycle 2 — Address a project whose name looks like an ID

- Scenario: create `deadbeef1234`, open its project glance by name, pause it by
  name, and confirm the pause using its actual ID.
- Initial reproduction: an isolated real app accepted the project name and
  assigned an unrelated ID. `cli(client, "project", "deadbeef1234")` then raised
  HTTP 404 at `/api/projects/deadbeef1234`.
- Regression first: `uv run pytest -q tests/test_cli_usecases.py -k id_shaped`
  failed on that 404 before the write could run.
- Fix: resolve against actual accessible projects, prioritizing real IDs and
  then names. Removed the name-shape shortcut and its unused regex import.
- Verified: the same regression now reads by name, changes pause by name, and
  reads the saved change by ID. The existing name/ID and write-route coverage
  also passes (`4 passed` in the focused run).
- Self-review: one project listing per scoped command is a deliberate small
  cost for unambiguous resolution. The previous route-recording tests now
  assert writes without coupling their expectations to lookup implementation.
- Independent review follow-up: the default listing excludes archived
  projects. A new regression reproduced loss of archived-ID access. Resolution
  now includes archived records for stable ID access, prefers active matches
  for names, and lists IDs when several archived names are ambiguous.

## Cycle 3 — Correct an intake brief while accidentally passing approval flags

- Scenario: a scout proposes SQLite; the operator sends
  `hive intake demo -m 'Use PostgreSQL instead' --proceed`.
- Initial reproduction: through the actual CLI/app/runner protocol, the
  result task had `conversation_turn=proceed` and its instructions did **not**
  contain the PostgreSQL correction. Combining approval and message flags
  silently selected one action.
- Regression first: three tests exercised every pair of conflicting actions.
  They failed because the CLI attempted API actions instead of rejecting the
  conflicting command.
- Fix: argparse makes `-m`, `--proceed`, and `--approve` mutually exclusive.
- Verified: all conflicts exit 2 with argparse's conflicting-argument error
  and leave conversations/tasks empty. Existing single-action intake and
  project creation workflows also pass (`5 passed` in the focused run).
- Self-review: enforcement lives in the public command grammar; no new
  server state or action precedence is needed.

## Cycle 4 — Start a project with an invalid spend cap

- Scenario: `hive new bad-budget --repo https://github.com/o/r.git --budget
  nan`, plus negative and infinite values and `hive set --daily-budget`.
- Initial reproduction: `--budget -1` failed at the API budget patch but left
  `bad--1` in the project list. `--budget nan` raised a JSON serialization
  `ValueError` and also left its project behind. Both had already wired the repo.
- Regression first: eight CLI/application cases failed before the fix;
  they require an argparse exit and unchanged project listing after bad input.
- Fix: both budget flags accept only finite, nonnegative amounts before any
  API request. This complements the separate API validation fix and prevents
  the CLI's multistep `new` command from creating partial state for bad input.
- Verified: `9 passed` in the focused budget/normal-project-creation run.
  The real `uv run hive new … --budget nan` entrypoint now exits 2 with
  `budget must be a finite nonnegative number` without contacting a chief.
- Self-review: zero and fractional amounts remain valid; the two flags share
  one small parser rather than relying on HTTP serialization or API failures.

## Cycle 5 — Discover the chief while checking CLI/chief versions

- Scenario: no localhost chief, but the installed runner's configuration
  points to a reachable chief; then try again with every chief unreachable.
- Initial reproduction: the actual `main(["version"])` with an isolated
  HTTP transport reported the localhost target, `chief: null`, and
  `chief_error: connection refused`. Only localhost was attempted; the
  available runner target was ignored and the command exited successfully.
- Regression first: two entrypoint tests failed, one for discovery and one
  for the missing failure exit. The reachable target delegates through the
  HTTP transport seam to the real app's `/api/version` route.
- Fix: remove the command-specific catch-all. Version requests now use the
  normal chief discovery, connection failure, and HTTP error handling.
- Verified: the runner chief is selected and its actual version appears in
  JSON. Total connection failure exits 1 and names both attempted chiefs.
  `hive --version` still succeeds offline.
- Self-review and agent discussion: ordinary discovery semantics are simpler
  than maintaining a special partial-success format for one command. Local
  version discovery remains a separate existing flag.

## Combined validation and cross-agent integration

- `uv run pytest -q tests/test_cli_usecases.py tests/test_cli.py
  tests/test_cli_plan.py tests/test_plan_wait.py tests/test_version.py` covers
  all five cycles plus established CLI, chief, plan, runner, and wait flows.
  Final result: **100 passed** (9 existing TestClient timeout deprecation warnings).
- Concurrent API/plan improvements intentionally require canonical `claude`
  subscription identifiers and renewed approval after editing queued work.
  Updated the existing CLI workflow tests to exercise those new contracts,
  including explicitly approving an edited proposal before it executes.
- `uv run ruff check hive/cli.py tests/test_cli.py tests/test_cli_plan.py
  tests/test_cli_usecases.py` and `git diff --check` pass.
