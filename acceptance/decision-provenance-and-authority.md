# story: decision-provenance-and-authority [ui]
As an operator I can see what Hive assumed versus what I specified and reverse any Hive decision, while Hive proceeds autonomously on everything it is authorized to decide, so that I keep clarity over the human/AI boundary without slowing Hive down on reversible choices.

## Rules
- Every decision Hive makes instead of asking is a provenance-tagged entry in `wiki/decisions.md` with `source_type` (`user_provided | agent_proposed | code_derived | inferred`), `impact`, `reversibility`, `status`, and `expires_when`.
- Both outputs of spec critique land in the ledger: a human's answer as `user_provided`, a guess-and-flag as `agent_proposed` with high reversibility and an `expires_when`.
- The project has an agent-authority contract: a `must_ask` set (project default in `mission.md`, plus any `iteration.md` override) that Hive never decides on its own, and a `may_decide` set that is always Hive's.
- `guess_propensity` governs only the gray zone — categories on neither list. It cannot authorize guessing a `must_ask` category at any setting.
- The UI surfaces the ledger with a count split between operator-specified and Hive-assumed decisions, and lets the operator re-open any Hive assumption.
- Re-opening a Hive assumption (or its `expires_when` condition becoming true) turns it back into an inbox question and parks the dependent work.

## Examples
- Given Hive must choose a value for a reversible, low-impact detail Hive is authorized to decide
  When Hive proceeds without asking
  Then it records an `agent_proposed` ledger entry with `reversibility: high` and an `expires_when`, and does not file an inbox question
- Given a decision falls in a `must_ask` category from the authority contract
  When Hive reaches it during planning or building
  Then Hive parks the work item and files a structured question with options and a recommendation instead of guessing, regardless of the `guess_propensity` setting
- Given the ledger contains both operator-specified and Hive-assumed entries
  When I open the project's decisions view
  Then I see the split count, can filter by `source_type`, and re-opening a Hive assumption converts it to an inbox question that parks the dependent workstream
