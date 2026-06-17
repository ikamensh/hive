You are integrating an already accepted issue fix after the default branch moved and Hive could not merge the issue branch automatically.

Read `ISSUE.md`, inspect attachments if present, and review the accepted-review context above. Your job is not to redesign the fix. Your job is to make the existing accepted fix merge cleanly with the latest default branch while preserving both sides' intent.

First update your view of the repo:
- `git fetch origin`
- identify the default branch from `origin/HEAD`
- merge the latest default branch into the current issue branch

Resolve only mechanical or clearly local conflicts, such as moved imports, formatting, nearby refactors, renamed helpers, or two compatible edits that can both be preserved. Run the relevant tests after resolving, commit the integration work, and push the issue branch.

Do not guess when the conflict asks for a product, data, security, API, UX, migration, or behavior tradeoff. In that case, abort any in-progress merge if needed, comment on the GitHub issue with the specific decision or information needed, and reject.

Decide:
- **ACCEPT** if the branch is pushed and should now merge cleanly into the latest default branch.
- **REJECT** only if resolving the integration requires human information or a tradeoff decision.

End with exactly one line, nothing after it:
`REVIEW: ACCEPT` or `REVIEW: REJECT`
