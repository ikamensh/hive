# story: decision-provenance-and-authority [ui]
As an operator I can see what Hive assumed versus what I specified and reverse any Hive decision, while Hive decides everything outside the `must_ask` set by default, so that I keep clarity over the human/AI boundary without slowing Hive down on choices it can safely make.

## Rules
- Every decision Hive makes instead of asking is a provenance-tagged entry in `wiki/decisions.md` with `source_type` (`user_provided | agent_proposed | code_derived | inferred`), `impact`, `reversibility`, `status`, and `expires_when`.
- Both outputs of spec critique land in the ledger: a human's answer as `user_provided`, a guess-and-flag as `agent_proposed` with high reversibility and an `expires_when`.
- Authority is a single `must_ask` set (a global default floor, plus any `mission.md`/`iteration.md` override) that Hive never decides on its own. Everything else defaults to decide-and-log; there is no `may_decide` list.
- Hive may also stop on a decision that is not `must_ask` if it spots a clear danger, but only when the question names the concrete danger.
- `guess_propensity` tunes how readily Hive escalates a self-spotted danger; it cannot authorize deciding a `must_ask` category at any setting.
- The UI surfaces the ledger with a count split between operator-specified and Hive-assumed decisions, and lets the operator re-open any Hive assumption.
- Re-opening a Hive assumption (or its `expires_when` condition becoming true) turns it back into an inbox question and parks the dependent work.

## Examples
- Given a decision is not `must_ask` and Hive sees no clear danger in it
  When Hive proceeds without asking
  Then it records an `agent_proposed` ledger entry with `reversibility` and an `expires_when`, and does not file an inbox question
- Given a decision falls in a `must_ask` category, or Hive spots a clear danger in an unlisted decision
  When Hive reaches it during planning or building
  Then Hive parks the work item and files a structured question with the named danger or gap, options, and a recommendation instead of guessing, regardless of the `guess_propensity` setting
- Given the ledger contains both operator-specified and Hive-assumed entries
  When I open the project's decisions view
  Then I see the split count, can filter by `source_type`, and re-opening a Hive assumption converts it to an inbox question that parks the dependent workstream
