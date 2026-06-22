# Architecture

Condensed current understanding of hive's design. Originated from a design interview session, 2026-06-12; edit as implementation teaches us things. See `mission.md` for what hive is and `iteration.md` for the current goal.

## 1. Core concepts

- **Project** — a mission + iteration goal + a set of one or more git repos (multi-repo from the start). Operational wiring lives in the app DB: per-project policy (toggles), intake status, and the authoritative list of member repos (managed via the web UI; the wiki may describe each repo's role, but the DB list is the registry).
- **Spec home** — one git repo per project containing:
  - `mission.md` — high-level goal, rarely changes.
  - `iteration.md` — the current timeboxed goal (user stories / "using system, steps X lead to Y").
  - `iterations/` — archive of completed iterations, each with a short outcome note. Git history technically preserves this, but an explicit archive is better UX for agents and humans.
  - Spec-mode projects pass through intake before planning: a trusted scout agent runs in a repo checkout, reflects its understanding of mission / next iteration / next steps, interviews the user when needed, then pushes durable spec files after approval. See `wiki/project-intake.md`.
  - The iteration goal is set or confirmed *through hive* (web UI / `hive iterate` / intake), which is authoritative and clears goal-completion; the orchestrator then distills it into `iteration.md` and archives the prior one to `iterations/`. Hand-editing `iteration.md` via git is not observed in MVP (no webhook yet), so direct edits require an explicit import/critique refresh.
  - `wiki/` — condensed, current understanding of the project, curated by agents and corrected by the human. Includes `infrastructure.md` (deployed services, URLs, how to deploy, where logs/secrets live).
  - `input-log/` — raw user inputs (clarification answers, free-text feedback) preserved verbatim for later re-evaluation. The wiki is the distillation; the log is the source.
  - For a single-repo project, the spec home and the code repo may be the same repo (hive itself is the first example).
- **Org context** — a "related resources" text document at the organization level (shared internal infra, conventions, org-wide facts), injected into orchestrator context for every project. Unstructured text in MVP.
- **Modes** — per project: **Build** (work toward iteration goal; MVP) and **Maintain** (distill/refactor/test-suite care, drift repair; fast follow, designed-for but not in MVP). Finer-grained work-type toggles may come later.
- **Workstreams** — issue solving is a triggerable GitHub issues workstream inside any project, not a separate project mode. See `wiki/unified-project-work.md` for the target design and remaining migration notes. Testing is a planned third workstream kind (deterministic, capability-aware, files GitHub issues on failure) — see `wiki/testing.md`.

## 2. The core loop

Two layers, deliberately separated:

### 2.1 Supervisor (deterministic, no LLM)

Plain code that owns the project state machine and computes the state purely from facts in the DB:

- `working` — a task is running or being dispatched. Invariant: **if not blocked, a next task must be happening ASAP.**
- `blocked: questions` — work needs human answers (spec clarification, escalations).
- `blocked: resources` — all usable AI resources exhausted; carries a wake-up time = earliest quota-cooldown expiry, so the system resumes by itself.
- `blocked: infra` — (post-MVP) infrastructure drift/problem needs human action.
- `idle: goal complete` — the iteration goal is fully built; waiting for the human to set the next one.

The supervisor wakes the orchestrator on events: intake accepted, task finished, question answered, resource cooldown expired, heartbeat timer, (later) GitHub webhook.

### 2.2 Orchestrator (AI, stateful)

A high-intelligence model session that plans and decides: decomposes the iteration goal into workstreams, picks the next task, picks the machine and agent backend for it, adapts when a runner times out or rejects, decides when to ask the human. Orchestration may require more intelligence than the worker tasks themselves.

- **Stateful sessions are first-class** — providers allow session resume (kodo demonstrates this for all backends); reusing a session saves tokens and preserves orchestration context.
- **Everything is also logged to the DB.** If a session is unusable for any reason, the orchestrator cold-starts from the wiki + operational state. Session files are backed up to GCS after each invocation.
- All durable orchestrator knowledge must be written down (wiki, workstream states, decisions) — transparency, debuggability, and future GEPA inputs all depend on this.

### 2.3 Workstreams and concurrency

- The orchestrator decomposes the iteration goal into **workstreams** — coarse directions ("auth flow", "data ingestion") chosen to touch mostly-disjoint parts of the codebase. Each is a sequence of tasks.
- **Execution is serialized per repo**: one agent task at a time per repo, commit/push lands before the next task starts. No merge conflicts by construction. Different repos of one project can progress simultaneously.
- When a workstream hits a blocking question it is **parked** and the orchestrator picks the next task from an unblocked workstream — the system stays busy if any direction is clear.
- True parallelism within a repo (git worktrees, kodo-style parallel stages) is a later opt-in.

### 2.4 Intake and standard opening workstreams

Before the build orchestrator plans spec-mode work, a mandatory **intake scout** aligns the project with the user and pushes `mission.md`, `iteration.md`, and supporting wiki/input-log notes. Intake is a stateful runner-backed agent conversation, not an implementation workstream.

Once intake is accepted, the orchestrator may open:

1. **Workstream 0: spec clarification** — only if active work still exposes a material ambiguity missed by intake. It can use the same clarification protocol and may rerun spec critique on demand (see `wiki/spec-critique.md`).
2. **Workstream 1: infra bootstrap** — repo skeleton, test harness, CI — *sized to the project*. A narrow script project gets pytest and nothing else. Skipped when not warranted.

## 3. Clarification protocol

Upfront, batch-level ambiguity detection is the spec critique (`wiki/spec-critique.md`). The protocol below is the per-decision safety net when the orchestrator/worker hits ambiguity mid-build:

1. **Self-answer first** — make a few attempts to resolve it from existing material: prior human input, the wiki, the codebase. Often the answer is already implied.
2. If unresolved, post a **structured question** to the inbox: context, the gap/contradiction, proposed options with a recommendation. The workstream parks.
3. **Batch** — while blocked, prepare related questions so one human visit unblocks a maximally long stretch of independent work.
4. Answers are stored raw in `input-log/` and distilled into the wiki/spec — clarifications accumulate; a fresh session doesn't re-ask.

**Guess-propensity dial** (per project, never → always, usually in between): how much the agent guesses vs. asks. Modulated by **reversibility**: cheaply reversible choices (naming, internal structure) lean guess-and-flag; expensive-to-reverse ones (data models, external APIs, product behavior) lean ask.

In MVP the only channel is the web UI inbox (user visits the page, sees work stalled on questions). Email/messenger channels later.

## 4. Quality gate

- Every task ends with **verification by a different agent session** than the one that wrote the code: tests pass, acceptance criteria checked against actual behavior, architect-style review. Kodo's benchmark edge came exactly from independent verification (9 rounds of caught bugs in real runs).
- The verifier's checklist includes **anti-bloat**: "does this add complexity/tests/CI not justified by the spec?" is a rejection reason.
- Two landing modes encode the speed/safety trade-off (the autonomy toggle): **direct_push (fast)** lands work on the default branch immediately, and verification is the after-the-fact safety net — a REJECT queues a fix task. **pr (mature)** keeps each workstream's work on its own branch (`hive/<ws>`) with a PR; the verify task reviews that branch and the human merges. Both modes are gated at the finish line: `mark_goal_complete` is rejected in code unless every done workstream's most recent task is a verify that ACCEPTed.
- Failed verification loops back to the worker at most N times (~3, enforced: `create_task` refuses another work task once a workstream has 3 verify rejects without an accept since), then the workstream parks with a question ("can't get this to pass, here's why").
- A red main build (when CI exists) is an event that triggers a fix task.

## 5. Distribution: machines and runners

- A **machine** is the capacity unit the user recognizes: either a cloud server expected to stay online or a personal computer that may sleep, travel, or disconnect.
- A **runner** is the technical access link on a machine. It registers with the control plane and advertises **capabilities**: installed agent CLIs, loaded credentials and their licensing mode, auth freshness, machine specs. The product should not present runner as a peer type to cloud server or personal computer; normally there is one runner per machine.
- **Push semantics, pull transport**: the orchestrator assigns "task → runner X"; physically the runner long-polls for assignments and streams results back. Works behind NAT, no inbound ports. Timeouts/rejections surface to the orchestrator, which adapts (retry elsewhere, replan).
- **Escalation channel** (first-class): any agent can file "missing credential / infra problem / harness limitation" → orchestrator grants, rejects, or escalates to the human inbox. These complaints are also valuable logs for improving the harness.
- Post-MVP: hive provisions cloud-server machines itself (spin up a VM, install backends, inject credentials from the vault, enroll it).

## 6. AI resources

- **Resource registry** in the DB: credential + binding (which runner/human) + quota model (rolling-window / weekly-cap / monthly-$ / unknown) + current estimate.
- **Licensing modes differ per provider and evolve** — e.g. Cursor issues API keys that spend subscription quota anywhere; Claude Max is practically bound to the machine where the human logged in. A **provider rulebook** (human- and agent-maintained notes) tracks these evolving rules; hive should investigate and update them over time.
- **User resource policy**: e.g. "prefer subscriptions, fall back to API keys up to $X/day" or rich-user mode "just use API keys on cloud VMs."
- Estimates come from **observed usage** (per-task token/cost reports — kodo parses these per backend) and **error-driven cooldowns** (429/out-of-quota marks a resource exhausted with a reset ETA). No dashboard scraping, no per-provider quota API integrations.
- **Budget enforcement is best-effort**: hive stops at the soft limit it tracks; the hard backstop is the budget set in the provider's console.
- **Auth freshness is a human task**: "refresh login on machine X or that capacity is lost" appears in the same inbox as questions.

## 7. Credentials & secrets

- Credentials are **entrusted to hive centrally** (GCP Secret Manager as the vault) rather than scattered on machines. Target: orchestrator injects only the credentials a task needs into the runner; runners are cattle.
- MVP simplification: credentials manually placed on the enrolled machines (cloud server + personal computer); GitHub access is simply the user's own `gh` login on those machines. GitHub App (per-repo installs, short-lived tokens, webhooks) is the product path; the auth interface is designed to swap to it without touching anything else.

## 8. Infrastructure awareness

- `wiki/infrastructure.md` in the spec home is the source of truth for deployed services: URLs, GCP projects, deploy procedure, log/secret locations. Agents update it when they deploy; verification of deploy tasks means actually hitting the deployed URL.
- **Drift detection (post-MVP but committed)**: periodic inventory sync (e.g. GCP Asset Inventory) vs `infrastructure.md`; minor drift is self-repaired, otherwise the project enters `blocked: infra` with an alert — autonomy requires self-repair or a clear escalation, never silent rot.
- Prod deploys are gated by a per-project toggle (default off).

## 9. Web UI (web-first)

Screens, in priority order:

1. **Project list** — one row per project with the supervisor state badge: `intake`, `working (+current task)`, `blocked: questions (3)`, `blocked: resources (resumes 16:40)`, `blocked: infra`, `idle: goal complete`. The "is hive healthy" glance.
2. **Project page** — dedicated intake panel for spec-mode projects that are not yet active (repo picker / create private repo, trusted-scout resource status, latest brief, material questions, answer/correction composer, proceed-with-assumptions, approve-and-finalize), then workstream board (running / blocked / parked / done per stream), the **inbox** (clarification questions answered in place — **free-text-first with the agent's proposed options as accelerators** — plus escalations and infra alerts), activity feed (tasks with outcome, cost, links to commits/PRs and full traces), and the toggles panel: mode (build/maintain), autonomy (PR vs direct-push), guess-propensity dial, prod-deploy switch.
3. **Resources page** — vault credentials, runners and status, quota estimates and cooldowns, spend today/this week.
4. Deep trace inspection reuses kodo's JSONL viewer.

Deferred: analytics, multi-user management, notification channels, mobile polish.

## 10. Logging now, GEPA later

GEPA-style prompt optimization (reflective mutation from execution traces + natural-language feedback, Pareto frontier of candidates) is **post-MVP**, but the MVP builds the logging because it's needed for visibility anyway and it is where the future GEPA inputs live:

- **Prompt store with overlays**: each agent role (orchestrator, worker, verifier, intake scout, spec critic/adjudicator) has a versioned base prompt + optional **per-project** and **per-user** overlays (user taste, e.g. anti-bloat rules, travels across projects). Every task records the prompt versions it ran with. Later, GEPA mutates exactly the overlays — base prompts stay product code, overlays are the evolvable genome.
- **Episodes**: per-task full trace (kodo JSONL format), outcome (verified/rejected/abandoned), cost, duration, prompt versions.
- **Feedback**: explicit 👍/👎 + free text on any task/PR/question (free text matters most for GEPA); implicit signals — PR merged untouched vs amended, clarification answer contradicting an agent guess, verification rejection reasons.
- No automatic prompt self-modification in the live loop — per-project adaptation in MVP comes from wiki/spec accumulation only.

## 11. Deployment & stack

- **Control plane: one small always-on GCE VM** running docker-compose. That cloud server is also enrolled for API-key backends; the user's personal computer is enrolled for subscription-bound backends (e.g. Claude Max). Access via Tailscale (no public exposure, works from phone); no IAP/load-balancer ceremony in MVP.
- **State lives off-VM from day one**: **Firestore** for structured state (projects, tasks, questions, resources, episode index), **GCS** for blobs (orchestrator session backups, traces, archives). The VM is disposable: a fresh one re-attaches and resumes, losing at most an in-flight task. Secret Manager from day one.
- **Monorepo** (`hive`): `control-plane/` (Python/FastAPI — supervisor, orchestrator invocations, Firestore/GCS, GitHub ops), `runner-agent/` (small Python daemon), `web/` (React + Vite + TypeScript SPA), `deploy/` (compose, VM bootstrap).
- **Kodo is reused as a library, not as the orchestration**: its raw primitives — backend sessions (Claude Code / Cursor / Codex / Gemini CLI wrappers with session persistence, token/cost parsing, malformed-output hardening), agent = prompt + session + budget, JSONL trace format. Hive builds its own supervisor, planning, distribution, inbox, and UI on top.
- Migration path when product time comes: control-plane container → Cloud Run (min-instances 1), runners → real fleet, `gh` login → GitHub App, Tailscale → proper auth. All mechanical because everything is containerized and state is already in managed services.

## 12. Open questions

- Orchestrator backend choice for stateful high-intelligence sessions (API model with our own context store vs CLI session on persistent disk) — decide at implementation time; kodo has working examples of both.
- Exact workstream/task schema in Firestore; what the orchestrator may restructure vs what's append-only.
- How Maintain mode interleaves with Build on the same project (shared serialized queue, presumably) — design when Maintain lands.
- Provider rulebook format and how its updates are verified (it encodes ToS-sensitive decisions).
