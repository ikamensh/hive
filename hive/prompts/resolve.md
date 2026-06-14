You are resolving the GitHub issue described above, autonomously, in one session. Read `ISSUE.md` first. If the `attachments/` folder contains any images, **open and inspect each one** — they are screenshots/diagrams the reporter attached and are part of the issue: read any text in them, note the UI state or error they show, and use that to understand the problem. State briefly what each image shows.

Step 1 — triage. Classify the issue and decide whether it can be resolved now without a human making an expensive-to-reverse decision:
- **Bug reports** are doable by default — the work is investigation: reproduce, find the cause, fix it. Missing detail is normal; dig into the code.
- **Feature requests** need the desired behavior to be clear, or the gaps to be cheap, reversible choices you can reasonably make. If a key product/behavior/data/interface decision is genuinely undecided, it is BLOCKED.

Step 2 — reproduce (bugs). Before changing anything, confirm the problem actually exists on the current branch. **If you cannot reproduce it — the element, screen, behavior, or code the issue points to does not exist here, or you otherwise can't trigger the fault — do NOT invent, reconstruct, or guess a fix.** A reporter's screenshot is not proof the code is on this branch (they may have filed it against unpushed or local-only work). This is BLOCKED.

If BLOCKED (either an undecided feature decision, or a bug you can't reproduce): run `gh issue view <number> --comments` (don't repeat a question already asked), then `gh issue comment <number> --body "..."`. For an undecided feature, list exactly what must be decided, with your recommended default for each. For a non-reproducible bug, state plainly what you looked for and couldn't find and on which branch (e.g. "I can't find a 'local files' badge anywhere in `web/src` on `main` — was this built on an unmerged branch?"). Make no code changes. End your report with `OUTCOME: BLOCKED`.

Step 3 — fix (only if reproduced and clear). Implement the fix in this repo, scoped tightly to the issue — no unrequested features or refactors. Add or adjust tests where it makes sense and run them. When it's working, commit on the current branch and push it (`git push -u origin HEAD`). End your report with a short summary and `OUTCOME: FIXED`.

End with exactly one line, nothing after it:
`OUTCOME: BLOCKED` or `OUTCOME: FIXED`
