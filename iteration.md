# Iteration 1: MVP Hive-on-Hive dogfood proof

**Demo scenario (done):** Prove the MVP path with a real operator run using Hive itself as both the spec repo and code repo. Use Hive's existing supported runtime modes as appropriate: local mode for operator-driven setup and/or distributed mode where chief and runner split across processes or machines. Start Hive against managed GCP state, register/probe any usable trusted runner backend, run web-driven intake for Hive, approve finalized durable specs via direct push, then let Hive start planning/building until it produces verified progress or parks on a concrete question/resource blocker.

Verified progress may be either a tested code change or a durable spec-only update, as long as Hive independently verifies or clearly records the result. Direct push is acceptable for this MVP proof; PR mode is not required for this iteration. Either Codex or Claude is acceptable as the required scout/backend, as long as it is usable and trusted on the operator machine.

## User story sets

### Basics: operator preflight

These stories must pass before asking the operator to trust a real project run. They prove Hive can start from this working environment, expose its control surfaces, discover available agent backends, register them as runnable capacity, and distinguish "installed" from "usable".

1. **Start Hive against managed state.** As the operator, I can start the chief process from this repo without deploying a new stack, while all Hive state stays in Firestore and GCS. User action: run `hive doctor storage`, stop the other chief or wait for its leader lease to expire, then run the documented chief command with `HIVE_GCP_PROJECT` and `HIVE_GCS_BUCKET` set. Expected outcome: the API starts, `hive projects` and `hive resources` return the same managed data, missing managed-state config fails before startup, and failures are explicit if another leader already owns the store.

2. **Use an operator surface.** As the operator, I can drive the same basics from either the CLI or the web UI. User action: run `hive projects`, `hive resources`, and open the web app Resources page. Expected outcome: both surfaces show the same projects, runners, backend resources, cooldowns, and human todos; if the API is down, they show a clear unreachable/error state.

3. **List locally available agent backends before registration.** As the operator, I can ask the runner what agent CLIs it detects on the machine. User action: run the runner's backend detection/preflight command. Expected outcome: Hive reports detected backends from the supported set (`claude`, `cursor`, `codex`, `gemini-cli`) and reports none with a clear "no supported agents found" result rather than silently registering empty capacity.

4. **Register this machine as a runner.** As the operator, I can start a runner and have it register itself with Hive. User action: run `uv run python -m hive.runner` against the chief. Expected outcome: the Resources page and `hive resources` show an online runner named for this machine, with one resource row for each advertised backend, and runner endpoints reject missing or wrong `X-Hive-Token` values.

5. **Verify an advertised backend is really usable.** As the operator, I can run a cheap smoke check for each advertised backend before trusting it with project work. User action: ask Hive to probe one backend against a temporary local git repo with a tiny prompt that must produce a deterministic marker and no repo changes. Expected outcome: success marks that `(runner, backend)` as usable with time/result evidence; auth failures, missing CLIs, quota errors, or crashes mark it unusable/cooling down and create a concrete human todo when the fix requires the operator.

6. **Avoid fake progress when no usable agent exists.** As the operator, I can see that Hive waits instead of pretending to work when no registered usable backend can run a task. User action: create or inspect a pending task whose requested backend is absent, offline, cooled down, or failed preflight. Expected outcome: the project shows `blocked: resources`, no task is dispatched, and Hive either suggests an available backend or asks the operator to register/fix capacity.

7. **Recover runner state.** As the operator, I can stop and restart a runner without leaving work stuck forever. User action: restart the runner while a task is assigned or idle. Expected outcome: boot registration refreshes the runner heartbeat, requeues work that died with the old runner process, and leaves heartbeat-only registrations from a still-running process untouched.

### MVP demo workflow

The MVP is working when a single Hive-on-Hive run can produce evidence for these stories. Each story should have a task/trace, UI state, commit/PR, or test result that a verifier can inspect.

1. **Create, intake, and observe the Hive project.** As the operator, I can create or configure a project from the web UI using Hive's repo as spec/code repo, then see a truthful intake state on the project list and page. Acceptance check: project creation alone does not wake the build orchestrator; starting intake queues a trusted scout conversation; the scout brief/questions appear on the project page; approving the brief lets the scout push `mission.md` / `iteration.md` / `wiki/intake.md` / `wiki/decisions.md`; and only then does the orchestrator start planning workstreams.

2. **Clarify before building when underspecified.** As the operator, I can answer or correct the intake scout's material questions before work starts, or choose to proceed with explicit assumptions. Acceptance check: the scout self-answers minor questions, asks only material questions, keeps the current best mission/iteration brief visible, persists accepted answers and assumptions into the spec repo, and planning starts only after the accepted spec is pushed.

3. **See and reverse what Hive assumed versus what I specified.** As the operator, I can tell which decisions Hive made on its own and revisit any of them. Acceptance check: every assumption Hive makes instead of asking is a provenance-tagged entry in `wiki/decisions.md` (`source_type`, `impact`, `reversibility`, `status`, `expires_when`); the UI shows a count split between operator-specified and Hive-assumed decisions; Hive never decides a `must_ask` category on its own; and re-opening a Hive assumption turns it back into an inbox question.

4. **Keep agents busy without causing repo conflicts.** As the operator, I can watch Hive run ready work as soon as resources exist while keeping at most one running task per repo. Acceptance check: two tasks for the same repo serialize, tasks for different repos can dispatch in parallel, and parked/question-blocked workstreams do not prevent unrelated work from continuing.

5. **Run work through an independent verification gate.** As the operator, I can inspect a work task followed by a separate verify task that checks actual behavior and rejects bloat. Acceptance check: every work task is followed by a verifier task with acceptance criteria; failed verification loops back or parks with a clear question; and `idle: goal complete` is only reachable after final verification accepts.

6. **Land work according to the autonomy toggle.** As the operator, I can choose PR mode or direct-push mode and have worker instructions match that policy. Acceptance check: PR mode lands on a branch with a PR, and direct-push mode makes the pushed result and following verification visible.

7. **Handle runner capacity and cooldowns.** As the operator, I can see runners, backends, quota/cost estimates, and cooldowns, and Hive pauses only when no suitable resource is available. Acceptance check: a registered runner receives a matching task, a resource-exhausted result marks the backend unavailable with a wake-up time, and the project shows `blocked: resources` until capacity returns.

8. **Escalate human-only actions clearly.** As the operator, I can see credential, DNS, billing, or runner-login tasks with exact instructions and mark them done. Acceptance check: human tasks appear in the resources/inbox surface, distinguish org-wide from project-scoped work, and marking one done records completion without losing project state.

9. **Survive chief restarts.** As the operator, I can restart the chief or runner and have Hive recover from persisted state. Acceptance check: a restarted chief reloads projects/tasks/resources; a rebooted runner re-registers; in-flight tasks are requeued or failed with an event; and orchestrator history can cold-start from the spec digest.

10. **Expose the MVP through the web UI.** As the operator, I can use the project list, project page, and resources page to understand what Hive is doing without reading logs. Acceptance check: the UI shows project state, workstreams, questions, task activity, policy toggles, runners/resources, human tasks, and org context; if the API is down, it shows an unreachable state instead of silently lying.

11. **Finish an iteration and start the next one.** As the operator, I can tell when Hive believes the goal is complete, inspect the completion note, and set a new iteration goal. Acceptance check: completion requires no active/pending tasks or open questions, the state becomes `idle: goal complete`, and editing the iteration goal clears completion and wakes the orchestrator.

## Refinement drivers

After each MVP dogfood run, update the spec from the evidence above:

- If a story passes only through a hand-crafted/scripted path, refine the story until the real user path is explicit.
- If a verifier cannot point to concrete evidence for a story, either add instrumentation/UI/tests or cut the story from MVP scope.
- If a story repeatedly needs human interpretation, promote the ambiguity into `mission.md`, `iteration.md`, or `wiki/` before more code is written.
- If implementation adds behavior not needed by a story, delete or defer it unless it directly protects reliability, security, or debuggability of the demo run.

**IN:** single-VM chief (Firestore + GCS + Secret Manager), supervisor state machine, stateful orchestrator with cold-start fallback, mandatory spec-mode intake scout, Build mode with workstreams and serialized per-repo execution, multi-repo projects with spec-home repo, clarification inbox with batching and guess-propensity dial, provenance decision ledger (`wiki/decisions.md`) with operator-vs-Hive split, `must_ask` authority, verification gate with anti-bloat checklist, resource registry with observed-usage estimates and cooldown wake-ups, user's `gh` credentials, web UI (project list, project page, resources page), full episode logging with prompt versioning, VM-as-runner + laptop-as-runner, org-context document.

**OUT (post-MVP, roughly in order):** Maintain mode, drift detection + `blocked: infra`, GEPA optimization loop, Hive provisioning runners and injecting credentials from the vault, GitHub App + webhooks, notification channels beyond web UI, multi-user, within-repo parallelism via worktrees, finer-grained work-type toggles, automated provider-rulebook updates.
