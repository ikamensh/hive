# Iteration plans

Replace the iteration workstream's emergent per-invocation planning with a
durable, human-visible **plan**: an ordered list of plan items the AI proposes
(or the human writes), reviewed at whatever depth the human chooses, then
executed through the proven resolve → review → merge pipeline, fed directly
from the plan. Done = the item's work merged on the remote default branch.

No GitHub issues or PRs are in the loop. The principle: **GitHub is a source
of work in (humans filing issues on an adopted repo), never hive's internal
work ledger.** The external-issues workstream is unchanged; the plan does not
touch the repo's issue tracker. Mirroring plan items to GitHub for outside
visibility is a display feature for later, not a mechanism.

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
  a blueprint), and free-form notes. The human may rewrite every field. The
  plan item **is** the work item the pipeline executes — one record carries
  review state, then execution state; there is no linked twin to drift from.
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
3. **Approve** = every item approved. One store transaction queues all items
   (they are meaningful as a set), plus one commit of the plan document to the
   spec home — the durable record of what was approved. Activation is
   store-only, so it is genuinely atomic. Execution starts immediately.
4. **Execute.** Strict plan order, one item active at a time, through the
   existing pipeline: a resolve task (warm session) clarifies and builds, a
   review task (fresh session) accepts or rejects, accept lands the work
   branch on the remote default branch as a real merge commit. The runner
   receives the item document (title, story, constraints, notes) as its work
   doc — the same markdown-doc channel issue tasks use today. An item is
   `done` when its work is merged on the remote. Blocked/rejected states park
   the item with the agent's explanation on the item itself, surfaced through
   Needs-you — not as a comment on some external artifact.
5. **Amend.** Reality invalidates plans. The AI proposes amendments (add,
   cancel, split, reorder remaining items) which need the human's flip; the
   human amends directly, self-approved. Amendments are store flips — no
   external churn — and are diffs against what was approved: the honest
   answer to "what changed since I signed off".
6. **Complete.** All items done → plan complete → the AI drafts the next
   iteration's goal and plan → back to review. "Set the next iteration" stays
   one verdict.

## Data model

```python
class PlanStatus(StrEnum):
    draft = "draft"          # assembling / under review
    approved = "approved"    # items queued; executing
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
    # review phase (owned by the plan UI)
    proposed = "proposed"    # awaiting the human's flip
    approved = "approved"    # flipped; waiting for whole-plan approval
    # execution phase (owned by the pipeline; same machine as issue work)
    queued = "queued"
    resolving = "resolving"
    blocked_clarity = "blocked_clarity"
    reviewing = "reviewing"
    rejected = "rejected"
    done = "done"            # work merged on the remote default branch
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
    parked_reason: str = ""  # blocked/rejected: the agent's explanation
    authored_by: str         # "agent" | "human"
    edited_by_human: bool = False
    created_at: float
    updated_at: float
```

One record, one owner per phase: the plan UI owns the review states and the
content fields; from `queued` onward the pipeline owns the status exactly as
it does for issue work items. Content edits stop at approval (amendment path
after that); no field is written by both sides.

## Execution: the doc-fed pipeline

The resolve → review → merge state machine is hive-internal; the GitHub issue
was only ever its input envelope, comment channel, and a second source of
truth to reconcile. Plan items replace all three with things hive already
owns:

- **Input** — the work doc is synthesized from the item (title, story,
  constraints, notes) and ships to the runner over the existing
  markdown-doc field on `Task` (today `issue_doc` → `ISSUE.md`; generalize
  the name to `work_doc`/`WORK.md` when convenient — cosmetic).
- **Communication** — blocked/rejected explanations land in
  `parked_reason` + Needs-you, hive's one attention queue.
- **Truth** — the store owns item status; there is no external state to
  reconcile, no filing races, no provenance markers so the issue scan can
  skip hive's own filings.
- **Landing** — merge the work branch into the remote default branch (the
  same merge call issue work uses; that is transport, not integration). With
  `autonomy=pr` the landing step opens a PR instead — an orthogonal project
  policy, not part of this design.

Sequencing reuses the existing lowest-order-first advancement: plan items
carry contiguous `order`, one item in `resolving`/`reviewing` at a time per
iteration workstream. Externally-filed GitHub issues remain their own
workstream with today's behavior, side by side.

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
- **Execution mode** — a progress rail: done items (merge commit link), the
  active item (live agent task), queued items, blocked items with their
  Needs-you entry inline. Bounded per UI convention: active + next few +
  count link.

## Relation to existing docs

- Supersedes the "iteration work uses the LLM orchestrator to invent work
  items" leg of `wiki/unified-project-work.md`; the workstream layer itself is
  unchanged, and for the iteration workstream the plan item takes the
  work-item seat in its hierarchy.
- Complements `wiki/proactive-autonomy.md`: decision ledger and `must_ask`
  govern latitude *inside* an item; the plan governs *which* items exist.
- Updates the `wiki/ideal-ux.md` north star: its "verdicts" gain plan
  approval, and the north star's assumption that max-emergence is the only
  target is replaced by the dial — the blind path stays one action, so the
  original bar still holds at one extreme.

## Scope

**MVP:** `Plan` + `PlanItem`, propose/review/edit/approve, atomic store-only
activation, execution via the doc-fed pipeline, amendments (human direct, AI
proposed), review checklist + progress rail.

**Deferred until a real run shows the need:**

- Auto-approve policy for AI amendments (the dial as a per-project setting).
- `story_keys` → a "proven" state after merge (done means demonstrated).
- Nesting beyond the list.
- Mirroring plan items to GitHub issues for outside visibility or
  collaboration — display only, never the mechanism.
- Routing directives through the doc-fed pipeline too, retiring their
  file-an-issue detour — the same "GitHub in, not out" simplification.
- Plan-as-PR approval (approval = merging a PR to the spec home) — considered;
  dropped because it taxes every UI edit with a commit. The approved-plan
  commit keeps the durable-record benefit without the friction.
- Cross-repo parallel execution of independent items.

## Test plan

- Property: approve-all is equivalent to flipping every item individually —
  identical resulting plan and item states.
- Activation atomicity: approval queues all N items and commits the plan doc,
  or changes nothing and surfaces the error — never a partial set (store-only
  writes make this cheap to guarantee).
- Invariant: no iteration task is ever dispatched without an approved plan
  item behind it.
- Round-trip: the committed plan document and the store rows agree on content
  after approval; the synthesized work doc contains every content field.
- Amendment: cancelling a queued item is a store flip; adding one queues
  exactly one; `done` items are untouchable.
- Phase ownership: no code path writes content fields after approval, and no
  plan-UI path writes status once the pipeline owns it.
