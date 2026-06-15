# story: testing-real-github-issues [api]
As a Hive operator I can let testing file confirmed findings as real GitHub issues so that bugs flow into the existing issue-solving pipeline.

## Rules
- Confirmed bug findings create or update GitHub issues in the tested repo.
- Missing custom labels must not prevent issue creation.
- The created issue records the story key, severity, oracle, finding detail, and evidence references.

## Examples
- Given a confirmed testing finding for a repo that does not already have a `hive-test` label
  When Hive files the issue
  Then the issue is created successfully and is labeled when possible.
