# story: build-verify-land-policy [ui]
As an operator I can watch Hive build, verify, and land work according to policy so that useful changes move quickly without bypassing review.

## Rules
- Hive dispatches ready work as soon as suitable resources exist while keeping at most one running task per repo.
- Tasks for different repos may run in parallel when resources allow.
- Every work task is followed by an independent verify task that checks actual behavior against acceptance criteria and rejects unjustified bloat.
- A rejected verify queues a fix or parks with a clear question after the bounded retry limit.
- PR mode keeps each workstream's work on its own branch (`hive/<ws>`) with a PR, where the verify task reviews that branch before merging.
- Direct-push mode lands work on the default branch immediately, using verification as an after-the-fact safety net where a verification rejection queues a fix task.
- Both modes are gated at the finish line: `mark_goal_complete` is rejected in code unless every done workstream's most recent task is a verify that ACCEPTed.
- `idle: goal complete` is reachable only when there are no active tasks, pending tasks, open questions, or unaccepted completed workstreams.

## Examples
- Given two ready tasks target the same repo
  When both are eligible for dispatch
  Then Hive runs only one of them until the first task has landed or stopped
- Given a work task reports success
  When Hive continues the workflow
  Then a separate verify task reviews the result from a fresh session before the workstream can count as accepted
- Given the project autonomy is set to PR mode
  When a worker finishes a change
  Then the result is visible on a Hive workstream branch or PR instead of silently landing as an unchecked default-branch change
- Given the project autonomy is set to direct-push mode
  When a worker finishes a change
  Then the result is pushed directly to the default branch immediately, and a following verify task reviews the default branch after-the-fact
