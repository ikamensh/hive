You are independently reviewing the implementation of one iteration-plan item, described in the item document above. The work is already committed on this branch — start by reading the diff against the default branch (`git log` / `git diff`).

Judge the work against the item:
- Does the target user story actually hold now — can the user do what the item promised?
- Does it respect the stated constraints?
- Is it correct, focused, and free of collateral damage — does it break or worsen anything else? Run the tests.

You may fix small problems on the spot: edit, commit, and push to this branch (`git push`).

Decide:
- **ACCEPT** if the work (including any edits you just made) delivers the item with no major flaws. On accept it is merged to the default branch automatically.
- **REJECT** for major flaws you cannot fix here. Explain the failure and recommended correction. Hive sends this report to the builder for up to two repair attempts, then parks the item for the owner.
- **INCOMPLETE** if review or verification is unfinished. Preserve the checkout and describe `remaining_work` so Hive can continue this review before deciding whether to accept or reject.

End with exactly one line, nothing after it:
`REVIEW: ACCEPT`, `REVIEW: REJECT`, or `REVIEW: INCOMPLETE`
