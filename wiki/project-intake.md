# Project Intake

Spec-mode projects must pass intake before Hive starts planning or building.
Intake is the alignment step: Hive reads the repo or creates a spec home,
interviews the user when needed, writes the durable spec, and only then wakes
the normal orchestrator.

Issue mode is separate. It may see whatever spec artifacts live in the repo,
but it does not depend on this intake flow.

## Goal

Get to actionable work that is aligned with the user's vision.

Hive should not ask the user to paste a mission and first iteration into small
setup textboxes. Those are usually real documents. Sometimes they already live
in the repo as `mission.md`, `iteration.md`, `wiki/`, README/docs, or planning
notes. Sometimes the repo is empty and Hive needs to help create them. In both
cases the product surface is the same: a stateful intake scout asks the sharp
questions, reflects back its understanding, and finalizes durable spec files
after the user approves.

## User Journey

1. The user creates a draft project with a name.
2. The user chooses an existing repo, or asks Hive to create a repo.
3. For greenfield/no-repo projects, Hive creates one private GitHub repo by
   default and uses it as both spec home and code repo.
4. The user starts intake.
5. Hive queues an intake scout conversation on a runner.
6. The scout inspects the repo, runs cheap diagnostic commands, and may browse
   public docs when useful.
7. The scout returns a compact brief:
   - long-term mission
   - next iteration goal
   - likely next steps
   - assumptions
   - material questions, if any
   - evidence
8. The user answers, corrects the brief, or chooses to proceed with current
   assumptions.
9. Hive sends that message back into the same scout conversation.
10. When the latest brief looks good, the user approves it.
11. Hive tells the same scout to finalize the spec, commit, and push.
12. Hive wakes the normal orchestrator to plan from the durable spec.

The project page shows a dedicated intake panel until planning starts. The
normal workstream board, active-work inbox, and activity feed take over after
intake is approved and the spec is pushed.

## Scout Contract

The intake scout is a coding-agent style session running in a checkout of the
project repo. It uses the same runner/task execution machinery as other agent
work, not a separate control-plane-only LLM path.

The scout may:

- inspect the repo freely
- run cheap/read-only or diagnostic commands
- make local scratch edits while reasoning
- browse public docs for packages, APIs, frameworks, services, or public
  products referenced by the repo
- ask the user material questions
- after user approval, edit spec files, commit, and push

The scout must not:

- push before the user approves the latest brief
- deploy, send external messages, or make external writes during intake
- paste private repo contents, private issue text, secrets, or proprietary
  details into web searches
- create Hive workstreams or implementation tasks
- modify product code while finalizing intake specs

During intake, the scout should self-answer minor or cheaply reversible
questions from the repo, existing specs, code, docs, and public references.
Questions to the user are reserved for decisions whose answers would materially
change mission, next iteration, acceptance criteria, repo wiring, or expensive
product/architecture choices.

## Scout Prompt Shape

Initial turn:

```text
You are Hive's intake scout.

Goal: understand this project well enough that the user can confirm or correct
Hive before work starts.

Inspect the repo. Prefer mission.md, iteration.md, and wiki/ over README
guesses. You may run cheap diagnostic commands. You may browse public docs for
external packages/APIs/services, but do not leak private repo content.

Return a compact brief:

Mission:
The long-term vision.

Next iteration:
The concrete, verifiable next goal Hive should probably work toward.

Likely next steps:
3-5 high-level steps, not implementation tasks.

Assumptions:
Cheap or reasonable assumptions you made instead of asking.

Questions:
Only questions whose answers would materially change what Hive builds.

Evidence:
The files, commands, or public sources that shaped your understanding.
```

Follow-up user answer:

```text
The user responded during intake:

<answer>

Update your understanding. Self-answer minor follow-ups. Return the revised
brief and only the remaining material questions.
```

Proceed with assumptions:

```text
The user chose to proceed with current information and accepts the risk of
wrong assumptions.

Finalize the brief using current repo/spec context. Do not ask more questions
unless work would be impossible rather than merely risky. Clearly list the
assumptions you are making.
```

Approval/finalization:

```text
The user approved the latest intake brief.

Update durable spec files to match it. You may edit:
- mission.md
- iteration.md
- wiki/intake.md
- wiki/decisions.md (log each assumption and accepted answer with provenance)
- input-log/* intake records

Preserve coherent existing mission/iteration text. Rewrite stale or wrong
content when needed. Do not modify product code. Commit and push the spec
changes. Report the commit SHA.
```

## Conversations And Tasks

`Task` remains the execution unit: it is queued, dispatched to a runner,
cancelled, traced, costed, and marked done/failed.

Stateful workflows use an optional `AgentConversation`:

- `role`: `intake` initially
- `project_id`
- `repo`
- `backend`
- `model`
- current session handle when the backend supports resume
- transcript/summary fallback when true resume is unavailable
- status

Each scout turn is a `Task` linked to the conversation. The runner resumes the
backend session where possible; otherwise Hive replays the transcript and latest
brief into a fresh task. Standalone tasks remain valid for probes, preflights,
fresh verification, and independent review.

Issue resolve conversations are a likely reuse point, but issue mode does not
depend on intake. Review remains independent and fresh.

## Backend Policy

Intake is high leverage and should use only trusted scout models for now:

- Codex with `gpt-5.5`
- Claude with Opus

If neither is usable on an online runner, intake blocks on resources. There is
no weak-model fallback in the MVP.

## Readiness

Hive derives readiness from the scout output instead of asking the scout for a
magic label.

Ready to approve means:

- mission is stated
- next iteration is stated in concrete, verifiable terms
- likely next steps exist
- assumptions are explicit and logged with provenance in `wiki/decisions.md`
- the agent authority boundary is set: the `must_ask` categories Hive will not
  decide on its own are known (project default in `mission.md`, plus any
  iteration override). See `wiki/proactive-autonomy.md`.
- there are no remaining material questions

These are the five lean readiness checks — capability, actor, acceptance,
boundary, authority — in intake form. The first four were already implicit in
the mission/iteration/steps/assumptions criteria; the authority line is the only
addition, and it is satisfied once the project has an authority contract.

If material questions remain, the intake panel shows them with the current best
brief. The user can answer, correct the brief, or proceed with assumptions.

Approval means: "this understanding is good enough; finalize the spec from it
and push." The user approves the latest brief, not a precomputed diff. The git
commit is the durable diff; if it is wrong, the user can revert, force-push, or
ask Hive/another agent to correct it.

## Spec Persistence

After approval, the scout pushes directly in push mode. PR-mode intake can be
added later, but the MVP supports push mode only.

The scout may modify existing `mission.md` and `iteration.md` when the user has
approved the understanding. It should preserve coherent existing specs and
rewrite stale or wrong ones. Each assumption it made instead of asking is
recorded as a provenance-tagged entry in `wiki/decisions.md`
(`source_type: agent_proposed`, with `impact`/`reversibility`/`expires_when`);
each accepted user answer is recorded as `source_type: user_provided`. See
`wiki/proactive-autonomy.md`. Evidence and useful narrative context go to
`wiki/intake.md` or another appropriate wiki file, citing decision IDs rather
than restating assumptions inline. Raw user answers are preserved under
`input-log/`.

After the push succeeds, Hive wakes the normal orchestrator with a note like:

```text
Intake accepted and pushed at <sha>. Plan from the durable spec.
```

The orchestrator, not the scout, decomposes the iteration into workstreams,
chooses resources, queues implementation tasks, and owns the build loop.

## UI Shape

Before active work, the project page is an intake workspace:

- repo picker or "create private repo"
- runner/backend availability for trusted scout models
- latest scout brief
- material questions
- answer/correction composer
- proceed-with-assumptions action
- approve-and-finalize action

After intake is approved and pushed, the project becomes active and the normal
project page appears: workstreams, active-work inbox, activity feed, settings,
and resource state.

## Implementation Notes

Likely model/API additions:

- `TaskKind.intake`
- `AgentConversation`
- optional `Task.conversation_id`
- session-handle support in backend factories where available
- transcript replay fallback
- `POST /api/projects/{id}/intake/start`
- `POST /api/conversations/{id}/message`
- `POST /api/projects/{id}/repo` to create a private greenfield repo

The first implementation should reuse the existing runner long-poll, checkout,
kodo `Agent`, trace upload, cancellation, cost accounting, and result reporting
paths that issue mode already uses.
