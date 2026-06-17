# story: testing-episode-from-acceptance [ui]
As an operator I can run a testing episode from acceptance stories so that Hive tests the product like a user and files only confirmed defects.

## Rules
- Story refresh updates `acceptance/` from `mission.md`, `iteration.md`, `wiki/`, and `input-log/`, never from product code.
- Ambiguous acceptance becomes a question instead of a guessed pass/fail oracle.
- A test episode snapshots the story keys in scope so the run remains auditable if the spec changes afterward.
- Each sweep tests one story in an isolated local or Docker environment, records achieved fidelity, and uploads evidence such as command output, browser state, screenshots, video, console logs, or network logs.
- A UI sweep runs only on a resource with a usable backend and a probed browser capability, plus a probed Docker capability when the environment recipe requires a container.
- If Hive lacks a usable run recipe or required runner capability, the sweep blocks with a concrete question or human todo instead of recording a fake pass.
- Sweep tasks do not edit product code or land changes.
- Suspected bugs require independent reproduction before Hive files or updates a GitHub issue.
- UX-smell findings require independent adjudication before filing and may be recorded as constrained or rejected instead.
- The project UI shows story status, stale status, achieved fidelity, evidence, and linked issues.

## Examples
- Given `acceptance/` is missing in a spec home
  When a story refresh runs
  Then Hive creates a bounded first backlog of core user-facing stories from the intention artifacts only
- Given a sweep finds behavior that violates an acceptance example
  When an independent reproduction confirms it in a fresh environment
  Then Hive files or updates one deduplicated GitHub issue with the story key, repro steps, oracle, evidence, and trace links
- Given a UI story requires Docker but no runner has a probed usable Docker capability
  When the episode tries to schedule that sweep
  Then the story is blocked on resources and I see the missing capability as a human todo or attention item
- Given a story passed before but the spec changed afterward
  When I view the testing workstream
  Then the story is marked stale until a later episode tests it against the newer baseline
