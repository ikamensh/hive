# story: github-issues-workstream-run [ui]
As an operator I can trigger a GitHub issues workstream run so that Hive fixes selected repository issues one at a time without unsafe landing.

## Rules
- Issue solving is triggered from a GitHub issues workstream inside a project, not by creating a separate issue-mode project.
- Before a run, Hive preflights the repo, GitHub token write access, runner git push ability, and runner `gh` commenting auth.
- A scan fetches open issue titles, bodies, comments, and embedded image attachments, then snapshots the selected issue numbers or all issues open at trigger time.
- New upstream issues discovered after a run starts do not join that run unless I start another run.
- At most one issue in a GitHub issues workstream is resolving or reviewing at a time, and the lowest selected queued issue starts first.
- A blocked resolve posts a GitHub comment explaining the missing clarification or unreproducible bug and makes no code change.
- A fixed issue gets a fresh independent review; accept merges into the default branch and closes the issue, while reject or landing failure leaves the default branch untouched and needs attention.

## Examples
- Given a project has a GitHub issues workstream for a member repo
  When I open the project Issues view
  Then I can preflight, sync, or start a manual issue run without changing the project's source mode
- Given I select issues 2 through 4 for a run
  When issue 5 is opened upstream during the scan
  Then issue 5 is visible in the workstream but is not started as part of the selected run
- Given the resolve agent cannot reproduce a reported bug on the working branch
  When it finishes the resolve task
  Then it comments on the GitHub issue with what it could not find, returns `OUTCOME: BLOCKED`, and Hive leaves the default branch unchanged
- Given a review accepts a fixed issue branch
  When Hive lands the issue
  Then the branch is merged into the default branch through GitHub, the issue is closed or confirmed already closed, and the next queued selected issue may start
