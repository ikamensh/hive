# Iteration plans

Replace the iteration workstream's emergent per-invocation planning with a
durable, human-visible **plan**: an ordered list of plan items the AI proposes
(or the human writes), reviewed at whatever depth the human chooses, then
executed through the proven issue pipeline. Done = merged on the remote.

## The dial: emergence ↔ certainty

The goal is to let hive work in **longer autonomous chunks**. What limits chunk
size today is trust: the orchestrator plans per invocation, so the human cannot
see far ahead and has to stay close. A plan approved upfront is the contract
that lets hive run a whole iteration without check-ins.

How much certainty the human buys is their choice per plan, not a setting:

- **Max emergence** — one click on *Approve all & start*, zero reading. The
  plan still exists: inspectable later, and the record of what hive intended.
- **Incremental** — approve item by item; a big plan is reviewed in pieces,
  with a running tally. Edit any part of any item along the way.
- **Max certainty** — write the plan by hand; hive executes it verbatim.

Both extremes must stay one-click cheap. There is deliberately no
`emergence_level` config — review depth *is* the dial.

The predictability invariant that makes blind approval safe over time:
**hive never executes iteration work that is not an approved plan item.**
Emergent freedom lives *inside* an item (the resolve session's latitude,
`guess_propensity`, the decision ledger — all unchanged); plan-level changes
are visible amendments.

## Concepts

- **Plan** — the ordered item list for one iteration. One active plan per
  project. `draft → approved → complete | abandoned`.
- **Plan item** — one durable unit of work: a high-level title plus its
  breakdown *inside the document* — target user story (who can do what once it
  lands), technical constraints (boundaries, deliberately underspecified — not
  a blueprint), and free-form notes. The human may rewrite every field.
- **List, not tree** — a list is the degenerate tree; start there. An item
  that proves too big is split into two items, not given children. Add nesting
  only when a real plan demonstrates the need.

Naming: `Task` already means one runner execution attempt, so the persisted
model is `PlanItem`. In UI copy "item" is unambiguous inside a plan view.

## Lifecycle

1. **Propose.** The AI drafts a plan when an iteration goal is set or changed,
   when the previous plan completes (proposing the *next* iteration with it),
   or on request. The human can also start from an empty plan and write items
   by hand.
2. **Review.** At chosen depth: approve all, or flip items one at a time.
   Edit, add, remove, reorder, split freely. Editing does not auto-approve —
   approval is always the explicit flip (approve-all being the shortcut), so
   "reviewed" keeps one meaning. Item approval is review progress, never an
   execution trigger.
3. **Approve** = every item approved. Atomically: commit the plan document to
   the spec home (the durable record of what was approved), file **all items
   together** as GitHub issues — they are meaningful as a set; a lone issue
   out of context misreads — and start execution.
4. **Execute.** Strict plan order through the existing resolve → review →
   merge pipeline, one item active at a time. An item is `done` when its issue
   is merged and closed on the remote. Blocked/rejected surfaces through the
   existing Needs-you machinery.
5. **Amend.** Reality invalidates plans. The AI proposes amendments (add,
   cancel, split, reorder remaining items) which need the human's flip before
   issues are filed/closed accordingly; the human amends directly,
   self-approved. Amendments are diffs against what was approved — the honest
   answer to "what changed since I signed off".
6. **Complete.** All items done → plan complete → the AI drafts the next
   iteration's goal and plan → back to review. "Set the next iteration" stays
   one verdict.

## Data model

```python
class PlanStatus(StrEnum):
    draft = "draft"          # assembling / under review
    approved = "approved"    # issues filed; executing
    complete = "complete"
    abandoned = "abandoned"


class Plan(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    goal: str                # the iteration statement this plan serves
    status: PlanStatus
    proposed_by: str         # "agent" | "human"
    spec_ref: str = ""       # path of the committed plan doc in the spec home
    created_at: float
    approved_at: float = 0.0
    finished_at: float = 0.0


class PlanItemStatus(StrEnum):
    proposed = "proposed"    # awaiting the human's flip
    approved = "approved"    # flipped; waiting for whole-plan approval
    filed = "filed"          # issue exists; the issue pipeline owns execution
    done = "done"            # issue merged + closed on the remote
    cancelled = "cancelled"  # removed by amendment, or plan abandoned


class PlanItem(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    plan_id: str
    order: int
    repo: str = ""           # target repo; the project's main repo when empty
    title: str
    story: str = ""          # target user story
    constraints: str = ""    # technical boundaries, intentionally sparse
    notes: str = ""
    story_keys: list[str] = []  # acceptance stories this claims to deliver
    status: PlanItemStatus
    authored_by: str         # "agent" | "human"
    edited_by_human: bool = False
    issue_number: int = 0
    issue_url: str = ""
    created_at: float
    updated_at: float
```

Execution state between `filed` and `done` (resolving, reviewing,
blocked_clarity, rejected) is **read from the linked issue work item, not
duplicated** — the proven state machine stays the only one that owns
execution; `PlanItem` carries review/landing state only.

## Issue filing

- All items file together at approval, in plan order. Each issue body is the
  item document plus a provenance marker (as directives do) and a line
  "Part of plan `<id>` — item N of M".
- The reconciled work items get `order` = plan order, so the existing
  lowest-order-first advancement executes the sequence with no new dispatcher
  logic.
- Externally-opened issues on the same repo coexist; plan issues take
  contiguous order first (they carry an explicit approval), external issues
  keep today's behavior.

## Planner changes

The orchestrator's iteration role shrinks from "invent and dispatch work" to
planning: `create_workstream` is retired for iteration use; in its place
`propose_plan(goal, items)`, `propose_amendment(...)`, and next-iteration
proposal on completion. `ask_user`, todo filing, and `mark_goal_complete`
remain; goal completion keys off plan completion. With no approved plan the
iteration workstream idles with the visible reason "no approved plan" — never
a silent stall. That shrinkage is where the predictability comes from: less
LLM-emergent surface, more deterministic machine.

## UI

- **Review mode** — a checklist. Each item expands to editable
  story/constraints/notes with provenance badges (agent-authored / you edited).
  Per-item approve flips; tally "4/7 approved"; sticky actions:
  *Approve all & start* · *Add item* · *Discard plan*.
- **Execution mode** — a progress rail: done items (PR link), the active item
  (live issue + agent task), queued items, blocked items with their Needs-you
  entry inline. Bounded per UI convention: active + next few + count link.

## Relation to existing docs

- Supersedes the "iteration work uses the LLM orchestrator to invent work
  items" leg of `wiki/unified-project-work.md`; the workstream layer itself is
  unchanged.
- Complements `wiki/proactive-autonomy.md`: decision ledger and `must_ask`
  govern latitude *inside* an item; the plan governs *which* items exist.
- Updates the `wiki/ideal-ux.md` north star: its "verdicts" gain plan
  approval, and the north star's assumption that max-emergence is the only
  target is replaced by the dial — the blind path stays one action, so the
  original bar still holds at one extreme.

## Scope

**MVP:** `Plan` + `PlanItem`, propose/review/edit/approve, atomic filing,
execution via the existing pipeline, amendments (human direct, AI proposed),
review checklist + progress rail.

**Deferred until a real run shows the need:**

- Auto-approve policy for AI amendments (the dial as a per-project setting).
- `story_keys` → a "proven" state after merge (done means demonstrated).
- Nesting beyond the list.
- Plan-as-PR approval (approval = merging a PR to the spec home) — considered;
  dropped because it taxes every UI edit with a commit. The approved-plan
  commit keeps the durable-record benefit without the friction.
- Cross-repo parallel execution of independent items.

## Test plan

- Property: approve-all is equivalent to flipping every item individually —
  identical resulting plan and item states.
- Filing atomicity: N approved items yield N issues with contiguous order, or
  zero issues and a visible error — never a partial set.
- Invariant: no iteration task is ever dispatched without an approved plan
  item behind it.
- Round-trip: the committed plan document and the store rows agree on content
  after approval.
- Amendment: cancelling an item closes its issue; adding one files exactly
  one; already-`done` items are untouchable.
