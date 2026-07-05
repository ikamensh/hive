# story: intake-before-planning [ui]
As an operator I can hand Hive a project spec and complete intake before planning starts so that Hive builds from an approved durable spec.

## Rules
- Project creation can accept the natural spec input the operator already has: pasted text, a file, or an existing repo with docs.
- Creating or configuring a project alone does not wake the build orchestrator.
- The project page lets me choose an existing repo or create a private greenfield repo before starting intake; a greenfield project gets a private repo by default.
- Intake runs as a trusted scout conversation and shows the latest mission, next iteration, likely next steps, assumptions, material questions, and evidence.
- The scout self-answers minor questions but asks the operator before decisions that materially affect mission, iteration, acceptance criteria, repo wiring, or expensive product choices.
- The scout does not push spec changes until I approve the latest brief.
- After approval, the scout may update only durable spec artifacts, logs accepted answers and assumptions with provenance, pushes a commit, verifies the pushed spec exists, and only then wakes the normal orchestrator.

## Examples
- Given I create a project with pasted spec text in the web UI
  When Hive saves the project
  Then the spec text is available to the first intake scout turn and no build workstream is planned yet
- Given I create or configure a project in the web UI
  When I do not approve intake
  Then no build workstream is planned and the project remains in an intake or draft state
- Given an intake brief has material questions
  When I answer or correct the brief
  Then the same intake conversation updates the visible brief and asks only remaining material questions
- Given the latest intake brief is ready to approve
  When I approve and finalize it
  Then Hive pushes `mission.md`, `iteration.md`, `wiki/decisions.md`, and supporting wiki or input-log updates, reports the commit, verifies the pushed spec, and then starts planning from that durable spec
