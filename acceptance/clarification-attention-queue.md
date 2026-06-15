# story: clarification-attention-queue [ui]
As an operator I can answer questions and complete human todos from one attention queue so that Hive resumes only when material blockers are resolved.

## Rules
- A material ambiguity creates a structured question with context, the gap or contradiction, proposed options, and a recommendation.
- A blocked workstream parks on its question while unrelated unblocked workstreams can continue.
- Raw answers are preserved in `input-log/` and the durable spec or wiki is updated with the distilled decision.
- Credential, billing, DNS, runner-login, and landing-failure actions appear as human todos with exact instructions.
- Human todos distinguish org-wide work from project-scoped work and can be marked done without losing project state.
- The project and Resources surfaces make `needs you` work visible without requiring log inspection.

## Examples
- Given a worker cannot choose between two expensive-to-reverse product behaviors
  When Hive asks for clarification
  Then I see a question with the decision context, options, and recommendation, and that workstream is parked
- Given one workstream is parked on a question and another repo has ready work
  When usable resources are available
  Then Hive can continue the unrelated work while the parked stream waits
- Given a runner login has expired
  When Hive detects the auth failure
  Then I see a human todo with the runner, backend, and refresh instructions, and capacity is not used again until the todo is resolved or the probe passes
