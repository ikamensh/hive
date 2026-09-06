# Iteration plans

Replace the iteration workstream's emergent per-invocation planning with a
durable, human-visible **plan**: an ordered list of plan items the AI proposes
(or the human writes), reviewed at whatever depth the human chooses, then
executed through the proven resolve → review → merge pipeline, fed directly
from the plan. Done = the item's work merged on the remote default branch.

The canonical agent interface is now a versioned TOML plan, shared by external
authors and Hive's planner. `plan-check`, `plan-import`, `plan-export`,
`plan-wait`, and `plan-result` cover the handoff and return path; Markdown remains
an initial-import shortcut. Retained revisions preserve the accepted input,
and deterministic result snapshots distinguish merged work from cancellations
without requiring a planner. The project goal verdict remains a separate
assessment. See [the artifact workflow](artifact-workflow.md) for current file,
amendment, recovery, and publication semantics.

Local execution supports independent build/review backend settings.
Included-only projects use their
CLI session grants at a zero dollar budget. Planning can use explicitly free
OpenCode models; paid API planning remains disabled. A configured
validation command runs on the runner after review and records evidence tied to
the pushed commit; landing uses that SHA. Review or validation failures trigger
two bounded repairs with preserved checkouts before parking. Quota/runner
interruptions instead resume the same stage with a separate attempt record.
Unfinished builds/reviews and invalid result reports get two bounded continuations
of the current stage, retaining their checkout and compatible session. Persistent
incompletion parks as rejected with execution evidence; a blocked build requires
an explicit owner question. Manual retry retains partial work and starts a fresh
session with the amended item document and prior report. The previous attempt's
history and usage remain unchanged.
See [local queue acceptance](../acceptance/local-queue.md) and the README for
the runnable workflow; the original design below describes the plan model.

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

1. **Propose.** The AI drafts a plan for the human's iteration goal, or the
   human writes the plan directly. A next-goal request is durable while its
   draft is pending, so restarting Hive cannot lose it. Drafting consumes that
   request; completion of the previous goal alone never authorizes another.
2. **Review.** At chosen depth: approve all, or flip items one at a time.
   Edit, add, remove, reorder, split freely. Editing does not auto-approve —
   approval is always the explicit flip (approve-all being the shortcut), so
   "reviewed" keeps one meaning. Item approval is review progress, never an
   execution trigger.
3. **Approve** makes approved items eligible for execution. The chief retains
   the input revision and publishes `iteration-plan.toml` to the spec home as a
   best-effort approval snapshot. Publication failure creates a todo without
   blocking execution. Subsequent revisions remain retrievable from the chief;
   they are not automatically synchronized into Git.
4. **Execute.** Strict plan order, one item active at a time, through the
   existing pipeline: a resolve task (warm session) clarifies and builds, a
   review task (fresh session) accepts or rejects, accept lands the work
   branch on the remote default branch as a real merge commit. The runner
   receives the item document (title, story, constraints, notes) as its work
   doc — the same markdown-doc channel issue tasks use today. An item is
   `done` when its work is merged on the remote. Blocked/rejected states park
   the item with the agent's explanation on the item itself, surfaced through
   Needs-you — not as a comment on some external artifact.
5. **Amend.** Export the current document and submit changes against that
   revision, retaining stable item keys. Stale edits, omitted items, and changes
   to active or terminal work are rejected. Changed queued instructions need
   renewed approval. Cancellation and retry remain explicit actions. Retained
   exports let the administering agent inspect what changed since approval.
6. **Complete.** Once all items are done or cancelled, the plan becomes complete
   if any work landed, otherwise abandoned. The execution result records that
   distinction and its evidence independently of the planner. A planner may
   assess whether the goal was met; the human sets the next iteration goal.

## Data model

The authoritative definitions are in [`hive/models.py`](../hive/models.py).
`Plan` identifies an iteration and its current accepted input revision;
`PlanItem` carries one durable item through review and execution.
`PlanRevision` retains canonical and submitted instructions. `PlanSubmission`
retains prepared writes only until the accepted revision is committed, allowing
an interrupted submission to recover. Each `Task` records `plan_revision`,
its attempt report, and validation evidence. Landed items record the merge SHA.

Runtime status does not belong in the editable document. Amendments change
eligible future instructions; running attempts retain the input they started
with, and completed work retains its evidence.

## Execution: the doc-fed pipeline

The resolve → review → merge state machine is hive-internal; the GitHub issue
was only ever its input envelope, comment channel, and a second source of
truth to reconcile. Plan items replace all three with things hive already
owns:

- **Input** — the work doc is synthesized from the item (title, story,
  constraints, notes) and embedded in `Task.instructions`, alongside retained
  context documents and advisory planning baselines.
- **Communication** — blocked/rejected explanations land in
  `parked_reason` + Needs-you, hive's one attention queue.
- **Truth** — the store owns item status; there is no external state to
  reconcile, no filing races, no provenance markers so the issue scan can
  skip hive's own filings.
- **Landing** — merge the accepted work into the remote default branch and
  retain the landing commit. Configured runner validation gates the exact
  pushed commit being merged. Plan execution currently uses direct landing;
  a PR delivery mode is not implemented for this pipeline.

Sequencing reuses the existing lowest-order-first advancement: plan items
carry contiguous `order`, one item in `resolving`/`reviewing` at a time per
iteration workstream. Externally-filed GitHub issues remain their own
workstream with today's behavior, side by side.

## Planner changes

The orchestrator proposes plans with `propose_plan(goal, items_json)` and
additional items with `amend_plan(items_json)`. Both use the shared document
schema and retained history. Its tools do not dispatch iteration work or
approve its own proposals. `ask_user`, todo filing, and `mark_goal_complete`
remain available. Completion can wake the planner to assess the outcome, but
the human must supply the next iteration goal before another plan is drafted.

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

**Implemented:** shared plan documents and retained revisions,
propose/review/edit/approve, ordered execution, guarded amendments, review
checklist and progress rail, pinned CLI waits, and deterministic result exports.

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
- Submission recovery: prepared writes recover before the accepted revision
  advances; rejected stale amendments leave the current revision intact.
- Publication failure: the chief retains approved input and executes it even
  when publishing the approval snapshot fails.
- Invariant: no iteration task is ever dispatched without an approved plan
  item behind it.
- Round-trip: the committed plan document and the store rows agree on content
  after approval; the synthesized work doc contains every content field.
- Amendment: cancelling a queued item is a store flip; adding one queues
  exactly one; `done` items are untouchable.
- Amendment ownership: active/terminal instructions remain unchanged;
  amended queued instructions require renewed approval.
