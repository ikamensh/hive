# story: plan-artifacts

As an operator working with an administering agent, I can prepare a plan in a
project repository, submit it to the selected Hive project, and reconnect to
inspect its progress and results without repeating planning or losing the
accepted instructions.

## Rules

- External agents and Hive's planner use the same canonical TOML document.
  Markdown goal/task headings remain a shortcut for initial input.
- The operator selects the project explicitly. A project can span multiple
  repositories; item repositories resolve at submission and remain in its input.
- `plan-check` validates files offline. Context documents are included as inline
  text. Repository baselines describe planning assumptions and are not verified
  checkout requirements or a mechanism for transferring local edits.
- Submission preserves the original source and a canonical accepted revision.
  The returned input digest hashes that revision's canonical export.
- Identical source submitted again to the same project reuses the accepted
  submission. Interrupted writes recover from the prepared submission before
  advancing the accepted revision.
- Amendments retain stable item keys and name their expected revision. Stale
  edits, omitted items, and changes to active or terminal items are refused.
  Changed queued instructions require approval again.
- `plan-wait` observes a specified plan with a bounded timeout, even when the
  project subsequently has another plan. Waiting does not mutate work.
- `plan-result` exports execution facts without invoking the planner. Done and
  cancelled items remain distinguishable; terminal is false while cancellation
  is draining. An execution result does not declare the higher goal successful.
- Local and managed chiefs use the same API and artifact contract. The chief
  owns live state; files are explicit inputs and dated output snapshots.

## Examples

- Given the same structured instructions authored locally or by Hive, when I
  export, parse, and submit the plan, then its prose, item identities, task order,
  repositories, and context retain their meaning.
- Given a lost submission response, when I repeat the same source after a chief
  restart, then I receive the same plan and accepted revision, with one execution.
- Given an interrupted submission write, when the chief recovers, then readers
  see the recovered accepted revision rather than a partially updated plan.
- Given two edits based on revision 3, when one becomes revision 4 and I submit
  the other, then Hive returns a conflict and directs me to export and reconcile.
- Given a running first item and queued second item, when I amend only the second,
  then the first attempt retains its original instructions and the second awaits
  renewed approval.
- Given an exported plan, when I delete an item section and submit it, then Hive
  refuses the omission and directs me to explicit cancellation.
- Given a completed plan followed by another draft in its project, when I wait on
  the completed plan's ID, then the observation concerns that completed plan.
- Given an included-only project with no planner, when its reviewed work lands,
  then the result contains the merge SHA, attempt reports, and validation evidence
  while the project goal verdict remains separate.
- Given a cancelled running plan, when its runner sends a final cancellation
  report, then the item remains cancelled and the result becomes terminal.
- Given a Markdown source and its accepted canonical export, when I retrieve
  `--submitted`, then I get the original text; hashing the canonical export yields
  the result's input digest.

## Verification

The maintained examples run in `tests/test_plan_document_format.py`,
`tests/test_plan_artifacts.py`, `tests/test_plan_results.py`,
`tests/test_plan_wait.py`, and `tests/test_plan_artifact_demo.py`.
`scripts/demo_plan_artifacts.py` additionally runs an isolated local Git example
with a scripted agent or an authenticated Codex/OpenCode agent. The recorded real
run and the boundary of that evidence are in [the workflow guide](../wiki/artifact-workflow.md).
