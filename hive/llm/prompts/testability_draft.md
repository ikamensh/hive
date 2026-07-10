You are drafting Hive's testability contract: `testability.md` at the repo root — the one place that states how to stand this product up for testing.

Explore the repo (docs, build files, CI config, scripts) and verify commands locally where cheap. Do not modify product code; the only file you write is `testability.md`.

Sections to write:

- `## Run` — a `### local` subsection with the exact commands from a fresh checkout to a running app; add `### docker` when the repo supports a containerized run. Prefer commands that accept an explicit port and note the default ports used.
- `## Health` — how to tell the app is up (URL + expected response, or command + expected output) and how long to wait.
- `## Reset` — how to return to a clean state (seed command, where state lives); write "stateless" if nothing persists.
- `## Credentials & accounts` — every secret or account a test needs and where it comes from. Never write secret values into the file.
- `## Constraints` — what tests must never touch (prod systems, real third-party accounts, destructive commands).

When a choice is genuinely the human's — a sandbox account to create, two plausible run modes, whether an external dependency may be exercised — do not guess silently. Write the contract assuming your recommended option and report the decision in your structured result so Hive can ask.

Commit and push `testability.md` when you changed it.

Structured result:

- `changed_files`: relative paths you changed (normally just `testability.md`).
- `commit_sha`: the pushed commit SHA when `changed_files` is non-empty, else "".
- `fidelities`: run fidelities the contract declares (`local`, `docker`).
- `decisions`: choices needing the human, each `{key, question, options, recommendation}` — short, self-contained options a non-reader of the repo can pick from.

End with `TESTABILITY: DONE`, or `TESTABILITY: BLOCKED` when the repo cannot be stood up at all (say why).
