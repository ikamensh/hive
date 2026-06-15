# story: intake-before-planning [ui]
As an operator I can create a project and complete intake before planning starts so that Hive builds from an approved durable spec.

## Rules
- Creating a draft project alone does not wake the build orchestrator.
- The project page lets me choose an existing repo or create a private greenfield repo before starting intake.
- Intake runs as a trusted scout conversation and shows the latest mission, next iteration, likely next steps, assumptions, material questions, and evidence.
- The scout self-answers minor questions but asks the operator before decisions that materially affect mission, iteration, acceptance criteria, repo wiring, or expensive product choices.
- The scout does not push spec changes until I approve the latest brief.
- After approval, the scout may update only the durable spec artifacts and pushes a commit before Hive wakes the normal orchestrator.

## Examples
- Given I create a draft project in the web UI
  When I do not start intake
  Then no build workstream is planned and the project remains in an intake or draft state
- Given an intake brief has material questions
  When I answer or correct the brief
  Then the same intake conversation updates the visible brief and asks only remaining material questions
- Given the latest intake brief is ready to approve
  When I approve and finalize it
  Then Hive pushes `mission.md`, `iteration.md`, and supporting wiki or input-log updates, reports the commit, and then starts planning from that durable spec
