You are independently reviewing a fix for the GitHub issue described above. Read `ISSUE.md` first; if the `attachments/` folder has images, **open and inspect each one** (they show the reported behavior — read text/UI state in them) and judge the fix against what they show. The fix is already committed on this branch — start by reading the diff against the default branch (`git log` / `git diff`).

Judge the fix against the issue:
- Does it actually resolve what the issue asked for?
- Is it correct, focused, and free of collateral damage — does it break or worsen anything else? Run the tests.

You may fix problems yourself on the spot: edit, commit, and push to this branch (`git push`). There is no back-and-forth with the original author, so prefer fixing small issues over rejecting.

Decide:
- **ACCEPT** if the fix (including any edits you just made) properly resolves the issue with no major flaws.
- **REJECT** only for major flaws or collateral damage you cannot salvage here. On REJECT, run `gh issue comment <number> --body "..."` explaining what went wrong and the recommended approach for the next attempt.

End with exactly one line, nothing after it:
`REVIEW: ACCEPT` or `REVIEW: REJECT`
