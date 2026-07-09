# story: finish-iteration [ui]
As an operator I can tell when Hive believes the iteration goal is complete, inspect the completion note, and set a new iteration goal so that each iteration ends cleanly and the next one can begin.

## Rules
- `idle: goal complete` is only reachable when there are no active tasks, pending tasks, open questions, or unaccepted completed workstreams.
- The web UI and CLI surface the completion state with a human-readable completion note that the operator can inspect.
- The completion note is the orchestrator-authored summary passed to `mark_goal_complete`; final verifier output is evidence for that note, not the canonical note itself. See decision `HIVE-MVP-009`.
- Editing or replacing the iteration goal (via the web UI or `hive iterate`) clears `idle: goal complete`, archives the prior iteration to `iterations/`, updates `iteration.md`, and wakes the orchestrator.
- Clearing completion does not destroy any completed workstream records, tasks, or decisions from the finished iteration.

## Examples
- Given all workstreams are accepted and no tasks or questions are open
  When Hive evaluates project state
  Then the project transitions to `idle: goal complete` with a completion note visible in the project UI
- Given the project is in `idle: goal complete`
  When I set a new iteration goal through the web UI
  Then the prior iteration is archived to `iterations/`, `iteration.md` is updated with the new goal, and the orchestrator wakes to plan the next iteration
- Given the project is in `idle: goal complete`
  When I inspect the project page
  Then I see the completion note, can review the iteration outcome, and am prompted to set the next goal
