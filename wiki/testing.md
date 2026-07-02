# Testing as a first-class workstream

Testing is a triggerable **testing workstream** inside any project, alongside the
iteration and GitHub-issues workstreams (`wiki/unified-project-work.md`). It is
deterministic (no orchestrator LLM in the path, like issue solving), runs as a
manually-triggered **episode** (a batch campaign, not reactive per-merge testing),
and **produces GitHub issues** — so a confirmed bug flows straight into the
existing resolve→review→land pipeline. The whole loop — refresh stories → sweep
as a user → confirm → file → fix → re-test — is built from parts Hive already
trusts.

## Why this exists

Building software with AI routinely produces features that were never executed
end-to-end the way a user actually would. Left to itself, an agent writes shallow
unit tests on the dev box and declares success. The goal here is **free leverage**:
the user adds no inputs, but gets higher-quality software because Hive
autonomously tests the product the way a user experiences it, in a realistic
fresh environment, and only escalates problems it has independently confirmed.

"Free" means free of *effort*, not free of *money*: episodes spend against the
project's `daily_budget_usd` like any other work.

## Principles

- **Test from intention, not from code.** Stories come from the spec
  (`mission.md`, `iteration.md`, `wiki/`), never from reading the implementation.
  Agents are *prompted* to behave like a user and not read the source (not
  sandbox-enforced — enforcement is too much fuss for too little gain). This keeps
  testing black-box in spirit so it catches "we built X but a user can't actually
  do X."
- **One story, one charter, evidence or it didn't happen.** Each sweep is a
  time-boxed exploratory session against one story, judged against acceptance
  criteria, producing machine-readable evidence (command output, DOM/role/console
  assertions, screenshots/video).
- **Testing finds; issue solving fixes.** A test task never edits product code.
  A finding becomes a GitHub issue (after confirmation) and the existing pipeline
  fixes it; a later episode re-tests and confirms green. Tester, fixer, and
  re-tester are different sessions — independent verification by construction.
- **Denoise before filing.** Cheap/fast agents do the broad sweep; nothing they
  flag reaches the issue queue until a second, independent agent confirms it (see
  the funnel below). This is what makes cheap horsepower trustworthy.
- **Highest feasible fidelity, recorded.** A pass is only as strong as the
  environment it was proven in. Fidelity is recorded on every result; a low-fidelity
  pass is visibly weak, not a fake green.
- **Capabilities are probed, not assumed.** Whether a runner can drive a browser
  or run Docker is proven by a probe and recorded per `(runner, backend)`, like
  the usability probe.

## Iteration scope

- **Iteration 1 (this spec):** manual episodes; spec-driven story refresh by a
  high-IQ model; charter-driven exploratory sweep by cheap agents; bug
  reproduction and UX-smell adjudication before filing; **Docker fresh-install +
  local execution + browser on the runner**; GitHub issues as output; a coverage
  view. One story per sweep task.
- **Iteration 2 (TODO, marked throughout):** ephemeral **distributed/cloud**
  environments (spin up DBs, services, machines from cloud-provider keys);
  **story grouping** so related stories share one environment setup; scheduled /
  Maintain-mode episodes; vision-based visual judgment; optional
  `mark_goal_complete` green-gate.

## Concepts: user story and acceptance

- **User story** — one user-facing capability stated as intention from the user's
  point of view: "As a \<role\>, I can \<accomplish a goal\> so that \<value\>."
  It names a journey a real person takes, in product language, with no
  implementation detail. It is the durable unit of the test backlog and doubles
  as the **charter** for an exploratory session.
- **Acceptance** — the externally observable conditions that prove the story
  works, written as concrete **examples** (Given/When/Then), judged only from the
  outside (UI/API behavior). Acceptance criteria are the **oracle**: the thing the
  sweep compares reality against to decide pass/fail.

The story is the "what & why" (the charter); acceptance is the "how we'll know,"
as concrete examples (the checkpoints). One story has one or more acceptance
examples.

## Methodology

The spine is established QA practice, chosen because each piece maps cleanly onto
one pipeline stage and reuses something Hive already has.

- **Specification by Example / BDD** (Adzic; North) — the artifact format. Stories
  + acceptance as Given/When/Then examples in business language, so a code-blind
  agent has a user-terms oracle. This is what `acceptance/` contains.
- **Example Mapping** (Wynne) — the *refresh* step. The smart model expands each
  story into **rules** (what must hold), **examples** (concrete scenarios to run),
  and **questions** (ambiguities it cannot resolve). Rules+examples become the
  test cases; **questions are posted to Hive's clarification inbox** as `Question`s
  rather than guessed — ambiguous acceptance never becomes a fake pass.
- **Scenario testing + Session-Based Test Management** (Kaner; Bach) — the *sweep*
  step. Each cheap agent gets a charter (one story) and runs a time-boxed
  exploratory session **acting as the user**, producing session notes + suspected
  findings. This is the antidote to superficial unit testing.
- **Oracle heuristics — FEW HICCUPPS** (Bolton/Bach) — how a code-blind agent
  decides something is wrong: does behavior match **Claims** (the spec), **User
  expectations**, **Product consistency**, **Purpose**, **Comparable products**,
  **Familiarity** (known bug patterns)? These are baked into the sweep prompt so
  "behave like a user" has teeth and findings are defensible.
- **Independent confirmation** — the denoise gate (reproduction for bugs,
  adjudication for UX smells) before anything is filed.

**Deliberate inversion of the test pyramid.** Standard advice is "lots of unit,
few e2e." Because an AI agent already over-rotates to unit tests, we intentionally
go **journey-first / e2e-heavy** — the usual "ice-cream cone" anti-pattern is
correct here, because the cheap unit layer is the agent's default and the
expensive journey layer is what it skips.

## The backlog: `acceptance/` in the spec home

Source of truth is the spec home (git, versioned, reviewable), one markdown file
per story under `acceptance/`:

```markdown
# story: login-with-google   [ui]
As a user I can sign in with Google so that I can access my dashboard.

## Rules
- A new user with a valid Google account ends up authenticated on /dashboard.
- A user who cancels the Google consent screen returns to /login with no session.

## Examples
- Given a fresh install and a valid Google account
  When I open /login, click "Continue with Google", and approve consent
  Then I land on /dashboard and see my account name
- Given I cancel the Google consent screen
  Then I return to /login and remain signed out
```

- **Tags route the test run.** The bracketed tag after the story key states how
  the user experiences the story and what capability a sweep needs: `ui` (browser
  — dispatched only to browser-capable runners), `cli` (terminal), `api`
  (programmatic); `docker` added alongside when the story needs a fresh
  containerized install. Tag `ui` only for journeys that genuinely happen in a
  browser (a mis-tagged CLI story parks its sweep as `blocked_resources`).
- **AI-first authorship, human-correctable.** The refresh step writes and updates
  these files; the human only corrects them. Human edits are preserved like
  `input-log/` — the refresh step reconciles against them and never silently
  overwrites a human-edited story (it proposes changes as a diff/question when it
  disagrees).
- Hive `reconcile`s `acceptance/` into durable `Story` records (cached status, not
  the source) the same way `issues.reconcile` mirrors GitHub issues: new file →
  `Story(untested)`; removed file → archived; changed acceptance → `stale` so the
  next episode re-checks it.

## Refreshing the backlog: baselines and prioritization

Refresh is the brain of an episode: it keeps the story backlog aligned to current
intention and decides which stories this episode actually sweeps. It is two parts
— an LLM **content reconcile** that runs only when intention moved, and a cheap
**deterministic prioritization** that always runs. Per-story memory makes both
cheap and stable. Each `Story` records:

- `spec_baseline` — a digest of the spec text the story was last aligned to (a
  **content digest, not a commit SHA**, so it survives the shallow spec clone).
  This is the "in good shape as of here" anchor.
- `blessed` / `blessed_at` — set when a human edits/approves the story, so refresh
  proposes changes to it as a diff/`Question` instead of overwriting.
- `centrality` (`core | major | minor`) — **model-derived each refresh** from the
  spec (named in `mission.md` = core; a journey many other stories depend on, e.g.
  login, is a hub; emphasis in `input-log/`), **human-overridable and sticky once
  overridden**.
- `last_tested_at`, `last_tested_baseline`, `last_fidelity`, `status`.

**Part A — content reconcile (LLM; skipped when the spec digest is unchanged).**
The smart model sees the current spec plus what changed since the stories'
baselines, and via Example Mapping it updates only affected stories, adds stories
for new capabilities, retires removed ones, posts ambiguities as `Question`s, and
advances each touched story's `spec_baseline`. Untouched stories stay byte-stable
(stability + cheap). **Code is not an input here** — story content comes from
intention only (the code-bias guard); code churn is at most a coarse re-test nudge
in Part B, never a content driver.

**Part B — prioritization (deterministic; always).** Each story gets a test
priority from, in descending weight: spec changed since last tested
(`spec_baseline ≠ last_tested_baseline`) → never tested → currently failing
(re-confirm a fix landed) → `centrality` → staleness (age since last test) → in
the active iteration's focus. By default an episode sweeps the **top-N
highest-priority stories** (`scope = priority`, N configurable); `full` (all
in-scope) and `selected` (explicit keys) are the alternatives. Spend is still
bounded by the supervisor's existing daily-budget gate, which stops dispatch when
the project is over budget — refresh only orders and bounds the set, it does not
do budget math.

A story is **stale** exactly when `spec_baseline` is ahead of
`last_tested_baseline` — a precise "green, but against older intention" signal a
bare timestamp can't give.

## The episode pipeline (deterministic; no orchestrator LLM in the path)

A `TestEpisode` is the campaign object (like `IssueRun`), snapshotting the story
set in scope so the run is auditable even if the spec changes afterward. Phases:
`refreshing → sweeping → confirming → done`.

1. **Trigger.** Human clicks "Run testing episode" on the testing workstream —
   or the **autonomous tick** does it: for a `testing_auto` project with a
   positive daily budget, the supervisor polls `auto_testing_action` (
   `TESTING_CHECK_INTERVAL_S`), which acts on the story-health verdict —
   missing/weak backlog → queue a story refresh, unproven stories → start a
   priority episode — with a per-kind daily cooldown (`AUTO_TESTING_INTERVAL_S`)
   and an in-flight guard, all judged from store facts so restarts never
   double-fire.
2. **Refresh (`test_refresh`).** Align the backlog and choose scope — see
   *Refreshing the backlog* above. The content reconcile runs the high-IQ model
   (reuse the `model_intel` "smartest available" selection that critique's
   adjudicator uses) **only when intention changed**, committing updated stories
   to the spec home and posting ambiguities as `Question`s; prioritization is
   deterministic. The chief reconciles `Story` records and snapshots the
   chosen story keys onto the episode.
3. **Sweep (cheap models, parallel, `test_sweep`).** One task per story (grouping
   is Iteration 2). The agent stands the app up in a **fresh Docker container**
   (or runs it locally) using the run recipe from the project's
   `wiki/infrastructure.md`, drives it through the acceptance examples **as a
   user** via the browser/CLI, and judges against acceptance + FEW HICCUPPS
   oracles. It uploads evidence, records the **fidelity** it achieved (`local` |
   `docker`) on the result, and ends with `SWEEP: PASS` or `SWEEP: FINDINGS` plus
   a JSON findings block (parsed by the existing `hive/llm` `extract_json`, as
   critique already does). Each finding is `bug` (spec violation) or `ux_smell`
   (suboptimal but not a violation), with severity, summary, repro steps, oracle,
   and evidence filenames. A PASS at `local` fidelity when `docker` was available
   is recorded as a weak pass, not a clean green.
4. **Confirm / denoise (independent agents).** Findings are persisted as `Finding`
   records (`suspected`) and run through the funnel below; only survivors file
   issues.
5. **Land.** A filed bug/UX issue is ordinary work for the GitHub-issues
   workstream. When a later episode's story passes, its open test issue is closed
   with a "re-tested green" comment.

Test tasks are **non-mutating to the product branch** (they only touch ephemeral
environments), so they deliberately **bypass the one-task-per-repo serialization**
that exists to prevent merge conflicts — sweeps fan out in parallel. The safety
requirement is per-task **environment isolation** (separate containers / ports /
namespaces), which Docker gives us in Iteration 1.

## Findings and the denoise funnel

A `Finding` is the intermediate object between a sweep observation and a filed
issue (the analog of an issue work item for issue solving) — it makes the pipeline
auditable and dedupable.

Two channels, because the noise profile differs:

**Bug channel (spec violation).**
- `suspected` → **`test_reproduce`** (independent agent, **fresh isolated env**):
  reproduce from scratch. The fresh env also filters false positives caused by
  state bleed in the sweep environment.
- Ends `REPRO: CONFIRMED` → file/update the GitHub issue (`confirmed`); or
  `REPRO: NOT_REPRODUCED` → drop as flaky/false (`rejected`, recorded for audit).

**UX-smell channel (suboptimal, not a violation) — the explicitly denoised path.**
A complaint about UX is noisy and often wrong or naive, so it goes through a
**reviewer adjudication** (`test_judge`) that, unlike the black-box sweep, *is*
allowed to investigate code/docs/constraints to reach a fair verdict. The
adjudicator answers two questions:
- **Does it agree with the complaint at all?** (Filters petty/mistaken smells.)
- **Is improvement feasible, or does the current limitation exist for a strong
  reason?** (A constraint with a good rationale is not a defect.)

Outcomes:
- `UX: IMPROVABLE` (valid and worth improving) → file a **low-severity** UX issue
  (`confirmed`).
- `UX: CONSTRAINED` (valid but the limitation has a strong reason) → **not filed**;
  recorded as a known-limitation note on the story with the rationale, so future
  episodes don't re-raise it.
- `UX: DISAGREE` (not actually a problem) → dropped (`rejected`, recorded).

**Dedup.** Findings dedupe per `(story_key, signature)`: a re-run updates the same
issue (new comment + latest evidence) instead of opening a second one; the open
issue number is tracked on the `Story`.

## Capabilities, resources, and environments

A real e2e test needs more than an AI backend — it needs a way to stand the system
up. Resources generalize from `(runner, backend)` to a bundle.

- **Capabilities** (Iteration 1: `browser`, `docker`) are discovered runner-locally
  (driver/CLI present, headless path / daemon available), advertised alongside
  `backends`, and **probed** authoritatively (a probe task launches the capability
  against a fixture and reports a marker + screenshot). Results are recorded per
  `(runner, backend)` on `Resource` (`browser_status`, `docker_status`,
  `*_probe_at`). Installed-but-unproven is visible to the operator but not
  dispatched.
- **Dispatch routing.** A `ui` sweep only goes to a resource whose backend is
  usable **and** `browser_status == usable` (+ `docker_status` when the recipe
  needs a container). If none exists, the work is `blocked_resources` with a
  concrete `HumanTodo` ("install Playwright / enable Docker on runner X"),
  extending `supervisor.compute_state`'s backend-availability check to a
  capability check.
- **Environment recipe.** How to stand the app up comes from the project's
  `wiki/infrastructure.md` (deployed services, run/deploy procedure). Iteration 1
  uses its Docker / docker-compose / local-run instructions for a fresh install.
  If there is no usable way to stand the app up, the sweep is `BLOCKED` → a
  `Question`/`HumanTodo`, not a fake pass. (Distributed/cloud recipes are
  Iteration 2.)
- **Perception constraint (kodo is text-only).** UI assertions must be
  **text-observable** — role/test-id/text selectors, visible text, console/network
  status, accessibility tree. Screenshots and video are captured as **human
  evidence and issue attachments**, not as the agent's perception. Vision-based
  visual judgment is a separately-probed capability for Iteration 2; until proven,
  no verdict depends on image content.
- **Blast radius.** Testing is **ephemeral-only and never touches production**,
  gated independently of `prod_deploys`. Cloud spend (Iteration 2) rides the same
  `daily_budget_usd`.

## Outputs / interfaces

- **GitHub issues (primary).** Confirmed findings file/update one deduped issue per
  finding signature, labeled `hive-test` plus `bug`/`ux`/`regression`. Body: story
  key + `spec_ref`, repro steps, the failing oracle, links to evidence artifacts
  and the task trace. Going green closes the issue.
- **Evidence artifacts.** Screenshots, video, console/network logs, raw output →
  blob store (like traces/attachments), served via
  `GET /api/tasks/{id}/artifacts/{name}`, linked from issue and UI.
- **Coverage view.** Project page surface: stories × last status (passing /
  failing / blocked / stale / untested), achieved fidelity, last-tested age, and
  the linked issue. The "is the product actually working" glance. CLI peer:
  `hive stories`.
- **Backlog health & the standing offer.** `story_health` (deterministic,
  server-side, `testing_health` in the project payload) turns the backlog into
  one verdict + offer: no stories → Hive offers to draft them autonomously from
  the spec; weak stories (`story_quality_problem`: no user intent, no concrete
  Given/When/Then examples) → Hive offers to rewrite them; untested/failing →
  run an episode. The web toolbar and `hive stories` render it; the offered
  action is always the existing refresh/episode trigger, one click/command away.
- **Activity + Needs-you.** Episodes/findings appear in the unified activity feed
  with a `[testing]` chip; ambiguous acceptance → `Question`, missing capability →
  `HumanTodo` — reusing the existing attention queue.

## Data-model & API additions

- `ProjectWorkstreamKind.testing`; one testing workstream per tested repo.
- `Story` (durable backlog unit; reconciled from `acceptance/`): `key`, `title`,
  `intent`, `acceptance`, `spec_ref`, `status` (untested|passing|failing|blocked|
  stale), `centrality` (core|major|minor), `centrality_locked` (human override),
  `spec_baseline` (digest), `blessed`/`blessed_at`, `last_tested_baseline`,
  `last_fidelity`, `open_issue_number`, `known_limitations`, `last_episode_id`,
  `last_result_task_id`, `last_tested_at`, `order`.
- `TestEpisode` (campaign, mirrors `IssueRun`): `scope` (priority|full|selected),
  `status` (refreshing|sweeping|confirming|done|cancelled|failed), `story_keys`
  snapshot, sweep/refresh backend+model, `counts`, timestamps.
- `Finding`: `episode_id`, `story_key`, `kind` (bug|ux_smell), `severity`,
  `summary`, `detail`, `oracle`, `evidence_blobs`, `status` (suspected|confirmed|
  rejected|constrained|duplicate), `issue_number`, `sweep_task_id`,
  `confirm_task_id`.
- `TaskKind`: `test_refresh` (smart), `test_sweep` (cheap), `test_reproduce`
  (bug confirm), `test_judge` (UX adjudication). Markers parsed deterministically
  (like `parse_resolve`): `REFRESH: DONE`; `SWEEP: PASS|FINDINGS` + JSON block;
  `REPRO: CONFIRMED|NOT_REPRODUCED`; `UX: IMPROVABLE|CONSTRAINED|DISAGREE`.
- Capability probe task + `Resource.browser_status`/`docker_status`; runner
  `capabilities` advertised alongside `backends`.
- `Task.artifact_blobs: list[str]` + `GET /api/tasks/{id}/artifacts/{name}`.
- Prompts: `prompts/test_refresh.md`, `test_sweep.md`, `test_reproduce.md`,
  `test_judge.md`.
- Deterministic GitHub helpers in `hive/_workstreams/testing.py` (kept apart from store ops,
  like `issues.py`): `file_or_update_finding_issue`, `close_story_issue`.
- Routes (mirroring issues): `POST .../workstreams/{id}/test-refresh`,
  `POST .../workstreams/{id}/test-episodes`, `POST /test-episodes/{id}/cancel`.

## Relationship to existing pieces

- **Maintain mode** is the natural home for scheduled/regression episodes
  (Iteration 2); Build mode runs episodes on demand. (`wiki/architecture.md`
  scopes Maintain as "test-suite care, drift repair".)
- **Quality gate.** A sweep is a focused, spec-anchored complement to the generic
  `verify` task. Future strong gate (Iteration 2, opt-in, default off):
  `mark_goal_complete` refuses while any in-scope `Story` is `failing`/`untested`.
- **Orchestrator** sees testing-workstream state read-only in its snapshot (like
  issues) so it won't call a goal complete over a red story, but it does not drive
  the testing loop.

## Open decisions / Iteration 2 TODO

- Distributed/cloud ephemeral environments + the cloud-credential resource model.
- Story grouping + shared-environment reuse for related stories.
- Scheduled/Maintain-mode episodes; vision capability + visual regression.
- `mark_goal_complete` green-gate on/off per project.
- Whether `test_reproduce`/`test_judge` should escalate to a stronger model only
  when the first confirmation is ambiguous (cost vs. accuracy tuning).
- Flakiness handling beyond single-shot reproduction (retry budget, quarantine).
