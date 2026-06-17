# story: build-verify-land-policy [ui]
As an operator I can watch Hive build, verify, and land work according to policy so that useful changes move quickly without bypassing review.

## Rules
- Hive dispatches ready work as soon as suitable resources exist while keeping at most one running task per repo.
- Tasks for different repos may run in parallel when resources allow.
- Every work task is followed by an independent verify task that checks actual behavior against acceptance criteria and rejects unjustified bloat.
- A rejected verify queues a fix or parks with a clear question after the bounded retry limit.
- PR mode lands work on a workstream branch with a PR, while direct-push mode follows the configured landing policy and still requires accepted verification before the goal can complete.
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

## Questions
- `iteration.md` says direct-push mode requires verification before pushing to the target branch, while `wiki/architecture.md` says `direct_push` lands on the default branch immediately and verification is an after-the-fact safety net. Which ordering should acceptance enforce?
