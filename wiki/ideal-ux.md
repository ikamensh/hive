# Ideal UX — spec-only autonomous development

The bar every hive flow is measured against, plus the live gap list. Written
upfront for the current iteration ("hive builds a project; the human only
writes specs"), updated after each validation project. Validation projects:
**A** greenfield TD game in Rust, **B** adopting the existing repo `gleaner`,
**C** a feature on the existing repo `kodo`.

## North star

The user's total contribution to a project is:

1. **A spec** — mission + what the next iteration should produce, in whatever
   form they naturally have it (a pasted document, a repo that already
   contains docs, a few sentences).
2. **Answers** to material questions, batched, asked only when the answer
   changes what gets built.
3. **Verdicts** — approve the intake brief, accept/reject demonstrated
   results, set the next iteration.

Everything else — repo creation, planning, machine/agent selection, building,
verification, landing, filing bugs against itself — is hive's job and never
appears as a required user step. A user step that exists because of hive's
internals (probing a resource, naming a runner, waking a planner that already
has everything it needs) is a UX bug.

## The ideal journey

1. **Hand over the spec in one action.** "New project" takes the spec
   directly: paste text, point at a file, or point at a repo. No name-only
   drafts that need three follow-up commands, no mission squeezed into CLI
   flags or tiny textboxes.
2. **One alignment conversation.** Hive reads everything it can reach first
   (the spec, the repo, public docs), self-answers what it can, then comes
   back once with: its understanding, its assumptions, and the few material
   questions. The user corrects/answers/approves in the same place. Approval
   ends setup — nothing else to arm.
3. **Hive builds, visibly.** Planning, dispatch, verification and landing run
   without the user. At a glance the user can see: what is running now and
   where, what it costs today, what happened recently, and why hive is *not*
   doing something (blocked on an answer / a login / budget / capacity) —
   stated as the reason, not as internal state names.
4. **Interruptions are rare and one-step actionable.** A material question, a
   `must_ask` decision, a login only a human can do. Each arrives with enough
   context to answer from where it is shown, and answering it resumes work
   without further ceremony.
5. **Done means demonstrated.** An iteration ends with evidence a human can
   check in minutes: the acceptance stories green, the app runnable with a
   one-line command, a demo artifact where visual (screenshot/recording).
   Setting the next iteration goal is one message, and the loop continues.

## Archetype A — greenfield from a written spec (TD game in Rust)

Ideal:

| # | User does | Hive does |
|---|-----------|-----------|
| 1 | Pastes the spec into "new project" | Creates private repo, seeds spec files from the pasted text, starts intake |
| 2 | Answers intake's one batch of material questions; approves brief | Pushes durable spec, plans workstreams, builds |
| 3 | (waits; answers rare questions) | Work→verify loop, lands green changes, keeps acceptance stories honest |
| 4 | Runs the one-line try-it command from the completion note | Demonstrated completion with evidence |

Current path (2026-07-05, before project A ran):

| Step | Verdict |
|------|---------|
| `hive create <name>` — draft with name only; spec has no entry point here | **friction** — the spec, the one thing the user has, cannot be handed over at creation |
| `hive repo-create <id>` — separate step | friction — should be intake's default for a repo-less project |
| `hive intake-start`, then `intake-send`/`intake-proceed`/`intake-approve` | ok in shape (web UI composes these); CLI-side it's 3–4 commands where the ideal is one conversation |
| Paste-the-spec into intake: possible only as a chat message to the scout | **friction** — a full spec document deserves a first-class "here is the spec" input that seeds `mission.md`/`iteration.md` directly |
| `intake-approve` auto-wakes planning (`intake.accept` → `supervisor.wake`) | ok — `hive start` is legacy for this path |
| Budget: `daily_budget_usd` defaults to 0 = *no cap* for planner/build spend, but autonomy (testing) reads 0 as *no envelope* and stays idle | **trap** — one number, two contradictory meanings; silent autonomy no-op |
| Resource plumbing: register runner, `hive resources`, `hive probe <resource_id>` before first work | **friction** — probing should be automatic on registration/first-need; only the human-required fix (a login) should surface |
| Completion: `goal_complete_note` + state badge | to verify in project A — must include runnable evidence, not a claim |

## Archetype B — adopt an existing repo (gleaner)

Ideal:

| # | User does | Hive does |
|---|-----------|-----------|
| 1 | Picks the repo in "new project"; sets posture (PR mode, budget) in the same screen | Reads the repo, drafts mission/iteration *from what already exists*, opens intake with that draft |
| 2 | Corrects/approves the brief | Pushes spec files (respecting the repo's conventions), plans |
| 3 | (waits) | Standing offers where the repo is weak: no acceptance stories → offer to draft; red CI → offer to fix; open issues → offer to work them |
| 4 | Reviews PRs | All landings are PRs in this posture |

The distinctive requirement: hive must feel like it *joined the project*, not
like the project was imported into hive — it adopts existing docs/CI/test
conventions instead of imposing its own scaffold, and its first acts are
offers grounded in what it found.

## Archetype C — a feature on an adopted repo (kodo)

Ideal:

| # | User does | Hive does |
|---|-----------|-----------|
| 1 | Types the feature ask into the launchpad box ("Give Hive a task") | Triages: material questions now or none; routes to an executor+machine; seeds work |
| 2 | Answers if asked | Build→verify→PR, directive tracked to done with the PR linked |

Current: `Directive` persists at `triaging` with a *preview* routing and
**nothing dispatches** (`wiki/project-launchpad.md` — brain intentionally
unbuilt). The launchpad's hero input is a UI promise the backend doesn't keep
yet. Project C is blocked on building the directive brain: triage →
route → seed a workstream/tasks → track to done.

## Cross-cutting requirements

- **Zero resource plumbing in the user path.** Machines register themselves;
  agents are probed automatically when first needed; the only human-facing
  capacity artifact is an actionable todo ("log claude in on hive-vm — run
  `hive login claude --machine hive-vm`").
- **One number for money.** A project's budget means the same thing to every
  spender (planner, build tasks, autonomous testing); spend-so-far is always
  visible next to it.
- **Reasons, not states.** `blocked_resources` etc. are internal; the user
  sees "waiting: no machine currently offers codex — your laptop is offline"
  with the fix attached.
- **Nothing silently pends.** Every accepted input (directive, answer,
  approval) visibly becomes work or visibly waits with a reason.
- **Evidence at completion.** Verify tasks and completion notes carry the
  command/URL/screenshot proving the thing works; "the agent said done" is
  not evidence.

## Gap list (live)

Numbered for reference from commits/fixes. Status: `open` | `fixing` | `done`.

| # | Gap | Archetype | Severity | Status |
|---|-----|-----------|----------|--------|
| G1 | No spec-first project creation: `create` takes a name only; a written spec has no direct entry (live run: one full scout turn burned to learn "repo is empty, what do you want?", and the spec had to travel as a chat message CLI arg) | A | high | done — `Project.initial_spec` + `spec_text` on create; spec injected into scout turn 1; `hive new <name> --spec f [--repo url] [--budget n]` one-step CLI; spec textarea on the web create form |
| G2 | Directive brain stubbed: launchpad's primary input dispatches nothing | C | high | done — a directive files a GitHub issue (provenance marker in body) + starts a selected-scope issue run; landing → `done`, external close → `cancelled`, filing failure → `triaging` with the reason |
| G3 | Budget semantics split: 0 = uncapped manual spend but disabled autonomy; silent no-op | A,B | med | done — one universal daily cap; new projects default $10; 0 pauses paid work (visible blocked_budget) |
| G4 | Manual probe step (`hive probe <resource_id>`) in the first-work path | all | med | done — was stale docs, not product: registration auto-probes unknown backends (`should_auto_probe`); README quickstart rewritten; `hive probe` stays as the after-a-fix re-check |
| G5 | `repo-create` is a separate user step for greenfield projects | A | low | open |
| G6 | No scheduled issue scan — new GitHub issues wait for a human `hive scan` | B,C | med | open |
| G7 | Completion evidence unverified: does `goal_complete_note` carry a runnable demo? | A | med | mostly done — live note carried claims, not a try-it line (the verify tasks did run real checks; the operator independently confirmed: fresh clone, 34 tests green, playable window). `mark_goal_complete`'s contract now requires a 'Try it:' line + evidence; deterministic enforcement deferred |
| G8 | `repo-create` (gh CLI path) made a commit-less repo; the scout's first checkout died on `origin/main is not a commit` — the recommended greenfield flow broke at turn 1 | A | high | done — gh path adds `--add-readme`; `checkout()` handles an empty origin (unborn branch, first push creates it) |
| G9 | CLI rough edges met on the way: every command prints chief-discovery noise; `hive trace` on a task that never ran an agent surfaces raw `{"detail":"Not Found"}` | all | low | open |
| G10 | Approval drifted into a two-command tail (`intake-write-mission` + `intake-approve`); the design doc's "approve = finalize and go" existed only as dead `finalize`-turn handling nothing queued | A,B | med | done — approve with missing spec files queues the scout finalize turn; its completion wakes planning |
| G11 | Chief verifies/reads the spec repo by *ssh* clone when the repo was wired via `repo-create` (stores `ssh_url`) — `Host key verification failed` on approve; the fleet's auth model is https+token everywhere | A | high | done — `authed_url` normalizes GitHub ssh remotes; `repo-create` stores the https clone URL |

| G12 | Zombie operator todos: a fixed condition (intake failure) left its "Needs you" entry open forever | all | med | done — a successful intake turn completes the project's intake-failure todo |
| G13 | `hive show` quoted a stale happy probe (`HIVE_AGENT_PROBE_OK`) as the reason a billing-blocked agent was down | all | low | done — auth-blocked work records its failure as the resource's latest usability evidence |
| G14 | Dispatch double-booked runners across projects (busy set was per-project): two tasks stacked on the laptop while the cloud server idled | all | high | done — running tasks count workspace-wide |
| G15 | A result POST lost during a chief restart stranded the task as `running` forever (live runners are never orphan-failed) | all | high | done — runner retries result reporting with backoff (report_result) |
| G16 | The 15-min autonomy tick cloned every budgeted project's spec repo before consulting any gate (incl. intake-stage projects) | all | med | done — envelope gates first, sync only inside the envelope |
| G17 | CLI had no directive command despite "full web-API parity" | C | low | done — `hive ask <project> <text>` |
| G18 | Chief trusted the finalize scout's "pushed" claim: gleaner's push 403'd (no org write access) yet intake went done and planning woke on an empty spec repo | B | high | done — finalize is verified against the actual repo (`spec_status`); failure reopens intake + files a todo naming the fix |
| G19 | Directive filing raced GitHub's eventually-consistent list API: issue created but the immediate selected run refused ("select at least one open issue") | C | med | done — work item seeded from what was just filed; reconcile grants just-created items a 3-min grace before reading absence as an external close |
| G20 | An intake retry starts cold: the fresh conversation re-asked material questions the user had already answered in the failed round (observed on gleaner's fork restart) | B | med | done — a new conversation carries the prior round's user answers; the scout's first prompt lists them as settled |
| G21 | Chief's spec-clone cache keyed by repo *name*: a fork colliding with its upstream slug made finalize verification read the wrong clone — false "spec files missing" right after a successful push | B | high | done — cache path carries a hash of the canonical URL; sync re-points origin every time |
| G22 | The planner queued work on backends that were installed but not usable (dead cursor subscription) — twice; each a stuck task needing an operator cancel | A | med | done — create_task validates against available resources on online runners; the error names what is usable |
| G23 | Zombie planner todos: of 16 open todos on the live board, 6 described conditions already gone (tasks cancelled, iteration complete) — only 4 narrow todo classes had any closing event | all | high | fixing — every todo carries a `kind` + machine-checkable `resolution` predicate; a supervisor sweep closes todos whose condition no longer holds |
| G24 | Duplicate todo fan-out: one root cause (dead cursor subscription) spawned 5 differently-worded todos across system + planner producers; title-exact dedup can't see them, and ids embedded in titles ("Cancel stuck task <id>") defeat it entirely | all | med | fixing — stable `dedup_key` (e.g. `access:<backend>:<runner>`) shared by all producers; planner `create_human_task` refuses to refile an open key |
| G25 | The planner filed todos asking the human to act on Hive itself ("run `hive task cancel …`", "answer question X") — actions inside its own API — and hallucinated wrong CLI syntax in the instructions | A | high | fixing — planner gets `cancel_task` + `withdraw_question` tools; access-todo instructions are rendered from server-side login recipes, not LLM memory |
| G26 | Todo "done" is claim-based: "Fix codex login on hive-vm" was marked done 3× and refiled each time because nothing verified the fix (only the login-probe path proves it) | all | med | fixing — resolution predicates make closure evidence-based where a store fact exists; manual done stays for `external` todos only |

(Gaps found during the validation projects get appended here.)

## Under the hood: efficiency findings (2026-07-05/06)

What the machines/resources/time audit found while the three projects ran;
each either became a fix above or a TODO note.

- **Dispatch double-booking (fixed, G14)** was the largest time sink observed:
  gleaner's intake queued ~40 min behind a rust-td verify on one laptop while
  the cloud server idled.
- **Dead credentials burned real work**: the dead OpenAI key cost a failed
  adapter attempt per planner invoke until its secret was disabled
  (orchestrator is Gemini-only now); the broken Cursor subscription failed a
  live work slice before the resource was parked — the failure now records
  itself as the agent's usability evidence (G13) and dispatch stops choosing
  it.
- **Planner overhead is small**: Gemini invocations run $0.03–0.06 each
  against $0.4–1.4 per build slice on the day's observations; rust-td's whole
  playable game cost ≈ $6.5 of API spend plus subscription quota.
- **Autonomy ticks no longer pay git for nothing** (G16): envelope gates run
  before any clone; the scheduled issue scan reuses the same discipline and
  never resurrects failed issues on its own.
- **A result POST is the only copy of an hour of agent work** — it now
  survives chief restarts (G15); deploys during live tasks are safe, and the
  reboot-requeue path was observed doing its job through a mid-claim restart.
- **Toolchain/warm-checkout-blind dispatch** remains open by choice
  (TODO.md): rust tasks landed on the only cargo-equipped machine by list
  order, not knowledge.
- **Throughput bound**: one agent per machine, serialized per repo — with two
  machines and three projects the fleet saturates; more machines is the lever,
  not scheduler cleverness.
