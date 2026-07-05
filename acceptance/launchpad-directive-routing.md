# story: launchpad-directive-routing [ui]
As an operator I can give Hive a free-form task from the project launchpad so that an accepted ask becomes tracked work instead of silently pending.

## Rules
- The configured project page presents the launchpad as the place to start work, with `Give Hive a task` as the primary composer and available job launchers such as `Fix issues`, `Run tests`, and `Advance build`.
- Submitting a directive persists the operator's ask and routes it by filing a GitHub issue on the project repo with a Hive provenance marker.
- Hive starts a selected-scope issue run for the filed directive issue instead of inventing a separate directive execution pipeline.
- The directive card links to the filed issue and tracks status as `triaging`, `working`, `done`, or `cancelled`.
- If routing cannot file or run the issue because of missing repo, token, preflight, or capacity, the directive stays `triaging` with the exact reason visible in the launchpad.
- When Hive lands and closes the issue, the directive becomes `done`; if the issue closes externally without Hive landing it, the directive becomes `cancelled`.
- The launchpad shows running jobs, machine checkout drift, the attention queue, and recent activity around the directive so the operator does not need logs to know what happened.

## Examples
- Given a configured project with a writable GitHub repo
  When I submit "Add a CLI command for testing episodes" in `Give Hive a task`
  Then Hive files a GitHub issue carrying the directive provenance marker, starts a selected issue run for that issue, and shows the directive as `working` with the issue link
- Given Hive cannot file the directive because the repo token lacks write access
  When I submit the directive
  Then the directive remains `triaging` and the routing note names the missing permission or preflight fix
- Given the directive's issue is merged and closed by Hive
  When the project payload refreshes
  Then the directive is shown as `done` and the activity feed links to the issue run and landing result
