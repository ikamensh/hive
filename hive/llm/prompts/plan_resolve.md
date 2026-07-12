You are building one item of an approved iteration plan, autonomously, in one session. The item document above states the target user story (who can do what once this lands), the technical constraints (boundaries, deliberately sparse — the how is yours), and notes. Earlier plan items are already merged on the default branch you branched from.

Step 1 — triage. The plan was approved by a human, so build it by default. The constraints are boundaries, not a blueprint: fill gaps with reasonable, reversible choices and note them in your report. It is BLOCKED only if a key product/behavior/data/interface decision is genuinely undecided and expensive to reverse, or the item assumes code/state that does not exist on this branch.

If BLOCKED: make no code changes. In your report, state plainly what must be decided or what is missing, with your recommended default for each open point — this report is exactly what the human sees to unblock the item. End your report with `OUTCOME: BLOCKED`.

Step 2 — build. Implement the item in this repo, scoped tightly to its story and constraints — no unrequested features or refactors. Add or adjust tests where it makes sense and run them. When it's working, commit on the current branch and push it (`git push -u origin HEAD`). End your report with a short summary — what now works, key choices you made — and `OUTCOME: FIXED`.

End with exactly one line, nothing after it:
`OUTCOME: BLOCKED` or `OUTCOME: FIXED`
