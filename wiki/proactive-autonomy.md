# Proactive autonomy

How Hive makes progress on the human's intention without waiting for a fully
specified slice, while keeping the boundary between *what the human specified*
and *what Hive assumed* durable and visible.

This is the spec-clarification loop's product principle made concrete. It builds
on machinery Hive already has — intake (`wiki/project-intake.md`), spec critique
(`wiki/spec-critique.md`), the `guess_propensity` dial, and the
orchestrator/verifier split — and adds three small primitives rather than a
parallel framework.

## Two north stars

1. **Clarity.** At any moment the human can see what they specified versus what
   Hive decided on its own, and reverse any Hive decision.
2. **Leverage.** Hive decides by default and logs it. Human input is reserved
   for the `must_ask` categories the human reserved, and for a danger Hive
   spots and can name in an otherwise-unlisted decision.

The readiness question follows from these. It is **not** "is everything
specified?" It is:

> Is enough known for this increment, and is each remaining unknown either
> (a) irrelevant to this increment, (b) decided by Hive and logged, (c) a
> `must_ask` category, or (d) a danger Hive named — the last two becoming
> questions?

## The three primitives

### 1. Decision ledger — the canonical form of every resolved clarification

Hive's spec critique already produces two outputs (`wiki/spec-critique.md`
Stage 3): a batched inbox question for the human, and guess-and-flag assumptions
for Hive. Today those diverge — one becomes `input-log/` + wiki prose, the other
becomes wiki prose, and the human/AI boundary dissolves into narrative the moment
a wiki page is rewritten.

Unify them. **Every resolution — a human answer or a Hive guess — lands as one
provenance-tagged entry in `wiki/decisions.md`.** The `source_type` field *is*
the human/AI boundary that north star 1 demands.

```markdown
## INV-001 · Invitation expiry
source_type: agent_proposed     # user_provided | agent_proposed | code_derived | inferred
impact: medium · reversibility: high · status: accepted_for_iteration
expires_when: user specifies a retention policy
trace: input-log/2026-06-17-intake.md#q3

Invitations expire after 7 days.
```

Fields:

- `source_type` — `user_provided` (the human stated it), `agent_proposed` (Hive
  guessed it), `code_derived` (read from existing code), `inferred` (deduced from
  other accepted decisions). This is the boundary the UI surfaces.
- `impact` — `low | medium | high`. How much a wrong call costs.
- `reversibility` — `low | medium | high`. How cheaply it can be changed later.
- `status` — `accepted` (human-confirmed), `accepted_for_iteration` (Hive's guess,
  good enough for now), `needs_clarification` (re-opened), `rejected`.
- `expires_when` — the condition under which this decision should be revisited;
  empty for durable human decisions.
- `trace` — link to the raw answer in `input-log/` or the critique finding.

Rules:

- The ledger is the structured index; `input-log/` keeps raw answers; wiki prose
  remains the readable narrative but cites decision IDs rather than restating
  assumptions inline.
- It lives in the spec home, so it rides the existing clone/digest/input-log
  distillation path — no new persistence.
- A human flipping any `agent_proposed` entry to `needs_clarification` re-opens
  it as an inbox question. Changing `expires_when`'s condition does the same.
- The orchestrator reads the ledger in its snapshot; an `accepted_for_iteration`
  guess is binding for the current iteration so two work items do not re-decide
  it differently.

The UI ("Needs you" / a spec view) filters on `source_type` to show, e.g.,
*"you decided 4 · Hive assumed 7 (6 reversible)"*. That filterable count is the
concrete realization of north star 1.

### 2. Agent authority — one `must_ask` list, decide everything else

The model is **bias-to-act, not enumerate-permissions**: Hive may decide
anything by default and logs it. There is exactly one explicit list — `must_ask`
— plus an escape hatch for dangers nobody listed. There is deliberately **no
`may_decide` list**: trying to enumerate what Hive is allowed to do is bloat and
is wrong by omission (the work it can do is open-ended). Two ways a decision
leaves the default-act path:

1. **Declared `must_ask` categories** — the human's standing line on *whose call
   it is*, independent of how reversible the choice is. A `must_ask` set is
   baked in as a **global default floor** (below); a project adds to it only
   when it diverges.
2. **A danger Hive names** — if Hive spots a clear risk in a decision that is
   *not* on any list, it stops and asks, and the question **must state the
   concrete danger**. "I'm unsure" is not a valid escalation; "this would let a
   non-member read another org's data" is. This mirrors the rule that you only
   stop to ask when you can name the specific blocker.

```markdown
## Agent authority — must_ask (global default floor)

must_ask:
  - who is authorized to perform an action (permission/auth model)
  - billing, pricing, or seat behavior
  - data retention and destructive defaults (hard vs soft delete)
  - public API contracts and breaking changes
  - legal/compliance wording and notices
  - security-sensitive defaults (e.g. token handling, account-existence leaks)

## Agent authority (project override — mission.md, usually empty)

also_must_ask:        # project-specific, e.g. fintech
  - rounding / interest computation
  - KYC and consent wording
```

The floor lives in the agent/verifier prompt, so most projects author nothing.
`must_ask` captures what `severity × reversibility` cannot: a permission or
plan-limit change is often trivially reversible in code yet is still the human's
call. `iteration.md` may add an **Authority** section to grant extra latitude for
a low-stakes slice ("this iteration may decide the invitation-email copy") or to
tighten a sensitive one.

`guess_propensity` no longer arbitrates a gray zone — the default is to act. It
now tunes only **how readily Hive self-escalates a danger it spotted** versus
proceeding and logging an `agent_proposed` decision: a cautious dial asks on
weaker dangers, a permissive one only on strong ones.

When a decision is `must_ask` or a named danger, Hive parks the work item and
files a structured question (context, the danger or gap, options, recommendation,
per `acceptance/clarification-attention-queue.md`) instead of proceeding. Because
`must_ask` is a behavioral guarantee — Hive cannot deterministically detect every
sensitive decision — the verifier's unauthorized-scope check is the backstop that
catches a `must_ask` decision made silently in a diff.

### 3. Lean readiness gate — one new check over what intake already verifies

The build-ready test is the operational definition of "Hive may start building a
work item." The lean form is five checks:

1. **Capability** — what becomes possible after this increment?
2. **Actor** — who can do it?
3. **Acceptance** — how do we know it works?
4. **Boundary** — what is explicitly out of scope?
5. **Authority** — is anything this increment needs a `must_ask` category?

Checks 1–4 are already Hive's intake-readiness criteria
(`wiki/project-intake.md` §Readiness): mission stated, iteration stated in
verifiable terms, likely steps exist, out-of-scope explicit. **Authority is the
only genuinely new check** — satisfied by default (decide everything) unless the
increment touches a `must_ask` category, which becomes a question. So the lean
gate is intake-readiness plus one line, not a separate rubric.

If a check fails, the planner asks only when the decision is `must_ask` or a
danger it can name; otherwise it decides, logs an `agent_proposed` entry, and
proceeds — it does not block on detail it is free to decide.

## Workflow

```text
Human intent (mission + iteration user stories + boundaries + authority)
   │
   ▼
Intake scout  ──►  mission.md / iteration.md / wiki/ / wiki/decisions.md
   │                (assumptions logged with source_type as they are made)
   ▼
Spec critique  ──►  inbox questions (top findings) + guess-and-flag entries
   │                 both land in wiki/decisions.md with provenance
   ▼
Readiness gate (5 checks) per work item
   │  pass ──► orchestrator plans, builds; verifier attacks (anti-bloat)
   │  fail ──► ask (must_ask or named danger) OR decide-and-log (everything else)
   ▼
Decisions accumulate; the human/AI boundary stays visible and reversible.
```

The human gives intent and the `must_ask` line. Hive turns that into work,
decides everything else on its own logic, asks only on `must_ask` or a danger it
can name, builds, and records every decision with its provenance.

## Scope

**In now (MVP):**

- `wiki/decisions.md` ledger with provenance fields, written by intake and
  critique, read by the orchestrator, surfaced and reversible in the UI.
- Agent authority: a global `must_ask` default in the agent/verifier prompt,
  optional `mission.md`/`iteration.md` overrides, enforced as a `must_ask` park
  plus the verifier's unauthorized-scope backstop. No `may_decide` list.
- The Authority check folded into intake-readiness — completing the lean gate.

**Design notes (build only when a demo run proves the need):**

- Full 5-gate formalization as a first-class scored object. Premature while the
  intake-readiness criteria + authority check cover it; revisit if a verifier
  cannot point to concrete readiness evidence for a work item.
- Build-time **implementation brief** inside a work item: before coding, the
  builder restates the goal, proposes the smallest clean implementation, lists
  *new abstractions with present-tense justification* (reject any justified only
  by hypothetical future use — `mission.md` anti-bloat), lists assumptions, and
  self-critiques once. This is a refinement of the existing orchestrator/verifier
  split; add it when a run shows the orchestrator making a hidden product
  decision today's critique missed, or shipping speculative abstractions.

Both deferrals follow the anti-bloat principle: do not duplicate the critique and
orchestrator/verifier machinery until a real run shows the gap.
