# story: testing-refresh-completes [api]
As a Hive operator I can refresh a testing backlog for a spec repo so that a new project reaches testable stories without manual babysitting.

## Rules
- Refreshing a testing workstream ends in reconciled stories, an explicit failed episode, or a concrete HumanTask.
- A refresh must not remain apparently running for several minutes with no checkout changes, no stories, and no actionable operator status.
- If the refresh agent cannot safely write acceptance stories, Hive explains the blocker.

## Examples
- Given a committed spec repo has `mission.md`, `iteration.md`, and `wiki/` docs but no `acceptance/` directory
  When I trigger refresh stories
  Then Hive creates stories or gives me a concrete blocked/failed state with instructions.
