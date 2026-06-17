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
2. **Leverage.** Hive does not ask the human about anything it is authorized to
   decide. Human input is reserved for choices that are product-sensitive,
   expensive to reverse, or genuinely ambiguous in a way Hive cannot resolve.

The readiness question follows from these. It is **not** "is everything
specified?" It is:

> Is enough known for this increment, and is each remaining unknown either
> (a) irrelevant to this increment, (b) authorized to Hive by the authority
> contract, (c) explicitly assumed and logged, or (d) isolated as a question?

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

### 2. Agent authority — a hard boundary that bounds the dial

The only autonomy control today is the scalar `guess_propensity`
(→ `ClarificationPolicy`). The missing piece is a **category boundary**: which
decisions are off-limits regardless of how eager the dial is, and which are
always Hive's.

```markdown
## Agent authority (project default — mission.md)

may_decide:
  - internal API and module shape
  - database table/column names and migration shape
  - test structure and coverage
  - local refactors that keep the implementation clean
  - ordinary validation and conventional HTTP status codes
  - minor UX details consistent with existing conventions

must_ask:
  - who is authorized to perform an action (permission/auth model)
  - billing, pricing, or seat behavior
  - data retention and destructive defaults (hard vs soft delete)
  - public API contracts and breaking changes
  - legal/compliance wording and notices
  - security-sensitive defaults (e.g. token handling, account-existence leaks)
  - major user-visible flow changes
```

The relationship to `guess_propensity` removes any redundancy:

- **`must_ask`** categories are *never* guessed, at any dial setting.
- **`may_decide`** categories are *always* Hive's, logged as `code_derived`/
  `inferred` decisions, never surfaced as questions.
- **`guess_propensity`** governs only the **gray zone** — everything on neither
  list — choosing guess-and-flag (→ ledger entry) vs. ask (→ inbox question).

So authority draws the boundary; the dial operates inside it. The durable
`must_ask` set is project-invariant and lives once in `mission.md`. `iteration.md`
may add an **Authority** override section to grant extra latitude for a low-stakes
slice ("this iteration may also decide invitation-email copy") or tighten it for a
sensitive one.

When Hive makes a `must_ask` decision it would otherwise have to guess, it parks
the work item and files a structured question (with options and a recommendation,
per `acceptance/clarification-attention-queue.md`) instead of proceeding.

### 3. Lean readiness gate — one new check over what intake already verifies

The build-ready test is the operational definition of "Hive may start building a
work item." The lean form is five checks:

1. **Capability** — what becomes possible after this increment?
2. **Actor** — who can do it?
3. **Acceptance** — how do we know it works?
4. **Boundary** — what is explicitly out of scope?
5. **Authority** — what may Hive decide by itself?

Checks 1–4 are already Hive's intake-readiness criteria
(`wiki/project-intake.md` §Readiness): mission stated, iteration stated in
verifiable terms, likely steps exist, out-of-scope explicit. **Authority is the
only genuinely new check** — and it is satisfied as soon as the project has an
authority contract (primitive 2). So the lean gate is intake-readiness plus one
line, not a separate rubric.

If a check fails, the planner either asks a short, material question (gray-zone or
`must_ask`) or makes a reversible assumption and logs it (`may_decide` or
gray-zone under a permissive dial) — it does not block on detail Hive is
authorized to decide.

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
   │  fail ──► ask (must_ask / gray zone) OR assume-and-log (may_decide)
   ▼
Decisions accumulate; the human/AI boundary stays visible and reversible.
```

The human gives intent and boundaries. Hive turns that into work, makes the
reversible and authorized decisions on its own logic, asks only material
product questions, builds, and records every decision with its provenance.

## Scope

**In now (MVP):**

- `wiki/decisions.md` ledger with provenance fields, written by intake and
  critique, read by the orchestrator, surfaced and reversible in the UI.
- Agent authority contract: project default in `mission.md`, optional per-
  iteration override in `iteration.md`, enforced as a `must_ask` park.
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
