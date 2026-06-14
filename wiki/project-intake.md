# Project intake and import

Hive project creation should not be a small form that asks the user to paste a
mission and iteration goal. In normal use the user is often importing an
existing repository, and the best available spec may already live in that repo
as `mission.md`, `iteration.md`, `wiki/`, README, docs, issues, or prior
planning notes. Creation should therefore be split from intake: first connect
the repo, then let Hive discover, critique, and clarify the spec before build
planning starts.

## Product principle

The durable spec is the product surface. The UI should show spec artifacts,
questions, source evidence, and status; it should not make the user manage a
large spec inside tiny setup textareas. Free text still matters, but as chat /
inbox input that is appended to `input-log/` and distilled back into the spec
repo.

For a repo like Hive itself, import should detect `mission.md`, `iteration.md`,
and `wiki/*.md`, then offer to run the clarity check. It should not ask the user
to re-enter that content.

## User journey

### 1. Create a draft project

The user starts from the Projects page and creates a project with just a name.
Hive creates a draft. No orchestrator wake, no worker tasks, no planning.

The draft page asks for the minimum durable wiring:

- spec/code repo
- optional member repos
- work source (`spec` or `issues`)
- autonomy, budget, guess propensity

Primary action: `Start intake`.

### 2. Start intake

The user clicks `Start intake`. This is the normal import path for both new and
existing projects.

Hive first runs the deterministic inspection phase:

- clone or refresh the repo
- validate read/write access
- record branch and HEAD commit
- detect whether the repo already contains Hive spec files
- collect bounded evidence from docs/spec/config files
- extract operational hints such as package manager and test commands
- produce a project dossier

The UI shows progress as "inspecting repo..." and then shows the dossier:

- inspected repo and commit
- detected spec files
- evidence files used
- explicit facts
- Hive inferences
- gaps and confidence

For Hive itself, this should immediately show that `mission.md`,
`iteration.md`, and `wiki/*.md` exist.

### 3. Vision check

After inspection, Hive runs alignment intake. The first user-visible output is a
vision check, not a task plan:

> Hive thinks the mission is X. The current iteration goal is Y. The first safe
> work appears to be Z. This is based on A/B/C. These are the unresolved
> decisions that may change what gets built.

The user can:

- accept the summary
- correct it in free text
- edit/replace proposed mission or iteration text
- answer specific questions
- ask Hive to re-read after pushing spec changes to the repo

Every correction is logged to `input-log/` and distilled into durable spec
files before planning.

### 4. Clarity check / interview

Hive runs the spec critique path against the accepted or current spec digest.
If the spec is already actionable, Hive says so and marks intake `ready`.

If not, Hive asks one batched inbox question focused on expensive-to-reverse
ambiguities. The question should include context, options, and a recommendation.
This is the productized version of the manual `grill-me` flow: sharp,
source-aware, and allowed to stop as soon as the first useful work is safe.

The user answers in the project inbox. Hive appends the raw answer to
`input-log/`, distills it into `mission.md`, `iteration.md`, or `wiki/`, reruns
or updates the critique, and repeats only if a real blocking ambiguity remains.

### 5. Ready to plan

Hive shows a `ready` intake state when all readiness checks pass:

- a mission exists or the user accepted a mission draft
- an iteration goal exists or the user accepted an iteration draft
- blocking critique findings are answered, dropped with reasons, or converted
  into explicit assumptions under the guess-propensity policy
- Hive can name at least one first task with observable acceptance criteria
- the user accepted or corrected the vision check summary

Primary action changes to `Start planning`.

The user can still stop here and edit the spec repo manually. If they do, they
click `Refresh inspection` or `Run clarity check` before planning.

### 6. Start planning

The user clicks `Start planning`. Only now does Hive wake the build
orchestrator.

The wake event says to plan from the durable spec, not from UI textarea payloads.
The orchestrator decomposes the iteration into workstreams, starts with any
remaining clarification/bootstrap work, and queues real tasks only after the
intake gate is clear.

At this point the project leaves intake and becomes active. The page shifts from
the intake panel to the normal workstream board, inbox, activity feed, and
settings.

### 7. Continue the loop

During active work, agents may still discover ambiguities. Those use the normal
clarification protocol: self-answer from the spec first, ask only when needed,
append answers to `input-log/`, distill to the spec, then continue.

When the iteration completes, Hive shows the completion note. The next
iteration follows the same shape, but starts from the existing spec home:

1. user gives a next-iteration note or pushes spec changes
2. Hive refreshes inspection/critique
3. Hive runs a smaller vision check
4. user accepts/corrects
5. planning resumes

### Happy paths

- **Existing Hive-style repo:** user selects repo, clicks `Start intake`, Hive
  finds `mission.md`/`iteration.md`/`wiki`, runs critique, asks zero or a few
  questions, then offers `Start planning`.
- **Repo with docs but no Hive spec:** Hive builds a dossier from README/docs,
  proposes mission/iteration drafts, asks the user to correct/accept them, then
  commits durable spec files and runs critique.
- **Thin repo or vague idea:** Hive cannot infer enough from repo evidence, so
  it starts the `grill-me` style interview and writes the resulting spec before
  planning.

### Failure / recovery paths

- **Repo access fails:** intake stops at inspection with a concrete access error
  and a retry action. No planning occurs.
- **Write access fails:** Hive can still inspect and ask, but files a human task
  or warning before it needs to commit spec changes.
- **Spec is contradictory:** critique produces a batched question and intake
  stays `needs_intake`.
- **User says Hive misunderstood:** the correction becomes raw input, Hive
  updates the spec/dossier, and the vision check is shown again.
- **User wants to force progress:** they can proceed with explicit assumptions;
  those assumptions are written into the spec before planning.

## Behind the scenes

Intake is a small pipeline, not the normal build orchestrator. Keep it lean:
repo inspection gathers evidence; the intake agent writes one compact
understanding brief.

### Phase 1: deterministic inspection

The control plane clones or refreshes the repo into its data dir. This can reuse
the existing spec-repo clone machinery, but should grow into a more general
read-only `RepoSnapshot` helper because import may inspect a code repo that is
not yet a Hive spec home.

This phase should not use a coding agent. It is plain code:

1. `git clone --depth 1` or `git fetch` + reset to the remote default branch.
2. Record repo URL, default branch, HEAD sha, clone time, and access/write
   checks.
3. Walk a bounded allow-list of evidence files:
   - Hive specs: `mission.md`, `iteration.md`, `wiki/*.md`, `input-log/*.md`
   - docs: README, docs, roadmap, TODO, AGENTS
   - project metadata: package files, pyproject, CI, Docker/deploy config
   - shallow file tree, excluding vendor/build/cache directories
4. Run small deterministic detectors for language, package manager, test/build
   commands, CI, deploy hints, and likely repo role.
5. Store a compact evidence bundle on the project: repo, branch, sha, detected
   spec files, selected excerpts, file tree summary, and obvious commands.

The evidence bundle is not a product artifact. It exists so the intake agent can
cite what it read without having to scan the whole repo.

### Phase 2: intake agent

After inspection, Hive runs an intake agent. This should be a control-plane LLM
flow at first, not a runner coding task: it is cheaper, faster, read-only, and
does not need a checked-out worktree with commit rights. Later it can be moved
to runner tasks if we want full trace parity.

The intake agent receives:

- the compact evidence bundle
- current spec digest, if native spec files exist
- org context
- project policy, especially guess propensity
- any prior intake answers from `input-log/`

The intake prompt should ask for a short understanding brief, not a full plan:

```text
You are Hive's project intake agent.

Goal: state what Hive understands about this project so the user can correct it
before Hive starts work.

Rules:
- Prefer existing Hive spec files over README guesses.
- State the long-term vision / mission in 1-3 paragraphs.
- State the next iteration goal in concrete, verifiable terms.
- List the next 3-5 likely steps, but do not create workstreams or tasks.
- Ask only questions whose answers would materially change the mission, next
  iteration, acceptance criteria, repo wiring, or expensive choices.
- Cite the few evidence files that mattered.
- Output JSON only.
```

The response should be small:

```json
{
  "status": "needs_user",
  "mission_markdown": "Long-term vision...",
  "next_iteration_markdown": "Concrete next iteration...",
  "next_steps": ["step one", "step two", "step three"],
  "questions": ["Question that must be answered before planning."],
  "evidence": ["mission.md", "iteration.md", "README.md"],
  "ready_to_plan": false
}
```

`status` should be one of:

- `ready` - no blocking questions; spec is actionable.
- `needs_user` - ask inbox questions or show a vision correction composer.
- `needs_write_access` - alignment is possible, but Hive cannot persist the spec.
- `inspect_failed` - no useful dossier exists.

### Phase 3: critique and persistence

If the intake brief looks coherent enough, Hive can run the existing spec
critique engine on the digest or proposed draft. The critique result may add a
batched inbox question or turn cheap ambiguities into explicit assumptions. This
should stay secondary to the brief; critique is a guardrail, not the main user
experience.

Persistence rules:

- Raw user answers go to `input-log/`.
- Accepted mission/iteration text is committed to `mission.md` and
  `iteration.md` when those files are missing or intentionally replaced.
- Assumptions that affect work are committed to the spec, usually under
  `wiki/intake.md` or the relevant existing wiki file.
- The project is marked `ready` only after the durable spec contains the
  accepted understanding.

Only after this pipeline marks intake ready does Hive wake the normal
orchestrator to create workstreams and queue tasks.

## Lifecycle

Add an intake lifecycle beside the supervisor state. Supervisor state answers
"is work running or blocked?"; intake state answers "is the project spec ready
to build from?"

Suggested values:

- `draft` - name exists, no repo configured.
- `importing` - Hive is cloning/indexing the selected repo(s).
- `needs_intake` - a repo is configured, but Hive needs spec extraction,
  critique, or human answers before planning.
- `ready` - the durable spec is buildable enough to start planning.
- `active` - build/maintenance work has started.

This should be explicit on `Project` rather than inferred from "no workstreams
and no tasks"; otherwise import, paused drafts, empty issue projects, and true
ready-to-plan projects all collapse into the same UI shape.

## One flow, two internal phases

The user-facing action should probably be `Start intake` or `Import project`,
not two required buttons. Under the hood it runs two phases:

1. **Inspect repo** - deterministic, cached, evidence gathering.
2. **Align intake** - agentic, user-facing, vision/goal clarification.

Keeping them separate internally matters because inspection should be cheap,
repeatable, testable, and mostly non-LLM. Alignment is where Hive spends model
tokens, asks questions, and proposes durable spec changes. In the UI they can be
one smooth flow: click once, see "inspecting repo...", then review Hive's
understanding and answer any sharp questions.

Expose `Inspect repo` as an advanced/retry action only when useful: access
failed, the repo changed, or the user wants to refresh evidence without running
another interview.

## What "Inspect repo" does

`Inspect repo` means "connect and inspect the repository enough to prepare an
aligned intake", not "start building" and not "silently invent a plan". It is a
read-mostly context-ingestion step with a concrete artifact at the end: an
evidence-backed project dossier.

The import output should answer:

- What repo did Hive inspect, at which commit?
- Is this already a Hive spec home?
- What does the repo itself claim the project is?
- What looks like the current goal, if anything?
- What docs/spec files were used as evidence?
- What commands, package managers, and test/build entrypoints are obvious?
- Which statements are explicit facts vs Hive inferences?
- What is missing before an agent can safely choose work?

Inspection should not queue worker tasks, create workstreams, run spec critique,
ask the user product questions, or call the project build orchestrator. It may
clone/index, read bounded files, validate access, and produce summaries. If it
writes anything, it should write only low-risk import metadata or a raw
`input-log/import-*.md` record; proposed spec changes should wait for
intake/acceptance unless the user chose an explicit "infer and commit" mode.

The state after inspection is not "ready to build"; it is "Hive has enough
evidence to run alignment."

Concrete inspection steps:

1. Validate repo access and record clone URL, default branch, HEAD commit, and
   whether Hive appears able to write.
2. Classify the repo: Hive spec home, code repo, or both.
3. Build a bounded evidence set:
   - Hive-native spec files: `mission.md`, `iteration.md`, `wiki/*.md`,
     `input-log/*.md`.
   - top-level README/docs/roadmap/TODO/AGENTS files.
   - package metadata, lockfiles, CI configs, Docker/deploy files.
   - a shallow file tree with ignored/vendor/build outputs excluded.
4. Extract operational hints: languages/frameworks, package managers,
   likely test/build commands, deployment hints, and linked/member repos when
   obvious.
5. Produce a dossier with sections for facts, inferences, gaps, evidence files,
   and confidence.

Inspection can use tiny deterministic parsers and heuristics; if an LLM is used
to summarize the dossier, every claim still needs an evidence link and must be
labeled as fact or inference.

## Import scan

Import scan is deterministic first, agentic second.

1. Clone or refresh the configured spec/code repo into control-plane storage.
2. Detect Hive-native spec files:
   - `mission.md`
   - `iteration.md`
   - `wiki/*.md`
   - `input-log/*.md` for prior raw answers
3. If native spec files exist, build the normal spec digest and mark the project
   `needs_intake` with "spec found".
4. If native spec files are missing or thin, mine candidate context from bounded
   repo evidence:
   - README and top-level docs
   - package metadata and commands
   - `AGENTS.md` / local agent instructions
   - roadmap, TODO, issue templates, design docs
   - a shallow file tree, not full source contents by default
5. Produce the project dossier: detected spec quality, source files used, likely
   member repos, project type, test/build commands if obvious, facts,
   inferences, gaps, and confidence.

The scan may create a draft `mission.md` / `iteration.md` only when the repo is
writable and the user explicitly accepts the draft, or when the selected import
mode says "infer and commit". In the default path, generated material should be
shown as proposed spec, then committed through the same intake flow as user
answers.

## Agentic intake

Agentic intake replaces the current "mission + first iteration goal" setup
fields.

Its job is alignment, not repository summarization. Given the import dossier and
current/draft specs, it should drive the project to a buildable, durable spec
that the user recognizes as their intent.

Inputs:

- the import summary
- the current spec digest, if any
- bounded repo evidence for non-native repos
- org context
- project policy such as guess propensity

Outputs:

- a concise "what Hive thinks this project is" summary with source links
- proposed `mission.md` and `iteration.md` changes, if needed
- a spec critique report
- at most one batched inbox question when human clarification is needed
- guess-and-flag assumptions that Hive can proceed with

The key UI moment after intake should be a vision check:

> Hive thinks the mission is X, the current iteration is Y, and the first safe
> work would be Z. These conclusions came from A/B/C. The unresolved decisions
> are Q1/Q2. Correct any of this before planning starts.

This is where a `grill-me` style interview belongs. The interview should be
source-aware, focused on expensive-to-reverse decisions, and allowed to stop as
soon as the spec is actionable enough. The goal is not a perfect spec; it is a
spec clear enough that the next tasks can be written with verifiable acceptance
criteria.

Intake should use the existing spec critique machinery after any proposed spec
draft exists. For an empty or thin repo, an intake interviewer can ask the same
kind of questions the user currently answers manually with the `grill-me`
workflow, but the UI should treat that as project intake, not as generic chat.

Planning is gated on intake:

- If the critique has blocking questions, the project stays `needs_intake` and
  shows the inbox.
- When the user answers, answers are appended to `input-log/` and the intake
  agent distills them into `mission.md`, `iteration.md`, or `wiki/`.
- When no blocking questions remain, Hive marks intake `ready`.
- The "start planning" action wakes the orchestrator from the durable spec,
  not from ephemeral setup fields.

Readiness should be explicit. A project is ready to plan when:

- `mission.md` or an accepted mission draft exists.
- `iteration.md` or an accepted iteration draft exists.
- every blocking critique finding is answered, dropped with a reason, or
  converted into an explicit assumption under the project's guess propensity.
- the first workstream can be described as a 10-60 minute task with observable
  acceptance criteria.
- the user has accepted or corrected the vision check summary.

## UI shape

The first project screen should be an import/setup console, not a prose form.

Recommended sections:

- Repos: spec/code repo picker, member repos, mode, autonomy, budget.
- Import status: clone/index state, detected spec files, import summary, errors.
- Spec preview: mission and iteration as document previews with source file
  links, not editable 3-line textareas.
- Intake inbox: batched clarification questions, free-text answers, proposed
  options, and assumption flags.
- Actions:
  - "Start intake" / "Import project" for a new configured project. This runs
    inspection first, then alignment.
  - "Refresh inspection" when repo evidence changed or access failed.
  - "Run clarity check" when spec files already exist.
  - "Continue intake interview" when no buildable spec exists yet.
  - "Start planning" only when intake is ready or the user explicitly proceeds
    with assumptions.

For small screens, the full-text input should live in the inbox composer or a
dedicated spec editor view. The setup panel should show status and summaries,
not try to host large documents inline.

## API and data model

Likely additions:

- `Project.intake_state`
- `Project.last_import_at`
- `Project.last_import_summary`
- `Project.last_critique_at`
- `Project.last_critique_sha`
- `POST /api/projects/{id}/import`
- `POST /api/projects/{id}/intake`
- `POST /api/projects/{id}/critique`
- `POST /api/projects/{id}/plan` or repurpose `/start` to mean "plan from
  current spec" with no mission/iteration payload

The current `/start` payload with `mission` and `iteration_goal` should become
a compatibility path, not the primary UI flow. If used, it should append the
raw brief to `input-log/`, distill to spec files, run critique, then plan only
after intake is clear.

## CLI shape

Mirror the UI:

```bash
hive create myproj
hive set <id> --spec-repo https://github.com/me/app.git
hive import <id>
hive critique <id>
hive answer <question_id> "..."
hive plan <id>
```

For fast demos, keep a shortcut:

```bash
hive start <id> --brief "..."
```

but implement it as import/intake/plan sugar instead of a separate mental model.

## Open decisions

- Whether generated draft specs are committed immediately under
  `input-log/import-draft-*.md`, or held only in DB until accepted.
- Whether intake should run as a control-plane LLM flow or as runner tasks. A
  control-plane flow is faster and can reuse `hive/llm`; runner tasks are more
  observable and reuse trace infrastructure.
- How much non-spec source code the importer may read before the user opts in.
  Default should be bounded metadata and docs, with explicit expansion when the
  intake agent says the spec cannot be inferred.
- Whether GitHub issues are part of spec-mode import evidence or only used in
  issues mode. For MVP, keep issues mode separate and let spec-mode import read
  docs first.
