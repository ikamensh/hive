# story: testing-artifact-links [ui api]
As a Hive operator I can open evidence artifacts from testing findings so that I can inspect screenshots, logs, and traces before deciding how seriously to treat a bug.

## Rules
- Evidence artifacts uploaded by a test task are linked from the finding detail in the UI.
- Confirmed GitHub issue bodies include usable links or references for evidence artifacts and the task trace, not just bare filenames.
- Missing artifacts should fail clearly instead of looking like evidence exists.

## Examples
- Given a test sweep uploads `.hive/artifacts/console.log` and reports it on a finding
  When I expand the story in the Tests view
  Then I can open the artifact from the finding detail.
- Given Hive files a GitHub issue for the confirmed finding
  Then the issue body includes enough artifact and trace information for a human to retrieve the evidence.
