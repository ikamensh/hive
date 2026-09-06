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
