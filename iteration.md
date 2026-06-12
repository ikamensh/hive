# Iteration 1: MVP

**Demo scenario ("done"):** create a greenfield multi-repo-capable project via the web UI with a mission and an iteration goal; hive interviews you to clarify the spec (workstream 0), bootstraps infra sized to the project (workstream 1), then builds as fast as resources allow — serialized per repo, verified per task, landing work via PR or direct push per the toggle — parking workstreams on batched questions you answer in the inbox, until the iteration goal is `idle: goal complete`.

## Verification stories

The MVP is working when a single demo run can produce evidence for these stories. Each story should have a task/trace, UI state, commit/PR, or test result that a verifier can inspect.

1. **Create and observe a project.** As the operator, I can create a project from the web UI by giving it a name and spec-home repo, then see it on the project list with a truthful supervisor state. Acceptance check: project creation wakes the orchestrator, the project detail page loads, and the state changes as workstreams/tasks/questions appear.

2. **Clarify before building when the spec is underspecified.** As the operator, I can see a batched clarification question, answer it in the inbox, and have hive resume work from that answer. Acceptance check: the question is persisted, answering it wakes the orchestrator, the workstream unblocks, and the answer is available to later tasks through the spec/context path.

3. **Keep agents busy without causing repo conflicts.** As the operator, I can watch hive run ready work as soon as resources exist while keeping at most one running task per repo. Acceptance check: two tasks for the same repo serialize, tasks for different repos can dispatch in parallel, and parked/question-blocked workstreams do not prevent unrelated work from continuing.

4. **Run work through an independent verification gate.** As the operator, I can inspect a work task followed by a separate verify task that checks actual behavior and rejects bloat. Acceptance check: every work task is followed by a verifier task with acceptance criteria, failed verification loops back or parks with a clear question, and `idle: goal complete` is only reachable after final verification accepts.

5. **Land work according to the autonomy toggle.** As the operator, I can choose PR mode or direct-push mode and have worker instructions match that policy. Acceptance check: PR mode lands on a branch with a PR and direct-push mode requires verification before pushing to the target branch.

6. **Handle runner capacity and cooldowns.** As the operator, I can see runners, backends, quota/cost estimates, and cooldowns, and hive pauses only when no suitable resource is available. Acceptance check: a registered runner receives a matching task, a resource-exhausted result marks the backend unavailable with a wake-up time, and the project shows `blocked: resources` until capacity returns.

7. **Escalate human-only actions clearly.** As the operator, I can see credential, DNS, billing, or runner-login tasks with exact instructions and mark them done. Acceptance check: human tasks appear in the resources/inbox surface, distinguish org-wide from project-scoped work, and marking one done records completion without losing project state.

8. **Survive control-plane restarts.** As the operator, I can restart the control plane or runner and have hive recover from persisted state. Acceptance check: a restarted control plane reloads projects/tasks/resources, a rebooted runner re-registers, in-flight tasks are requeued or failed with an event, and orchestrator history can cold-start from the spec digest.

9. **Expose the MVP through the web UI.** As the operator, I can use the project list, project page, and resources page to understand what hive is doing without reading logs. Acceptance check: the UI shows project state, workstreams, questions, task activity, policy toggles, runners/resources, human tasks, and org context; if the API is down, it shows an unreachable state instead of silently lying.

10. **Finish an iteration and start the next one.** As the operator, I can tell when hive believes the goal is complete, inspect the completion note, and set a new iteration goal. Acceptance check: completion requires no active/pending tasks or open questions, the state becomes `idle: goal complete`, and editing the iteration goal clears completion and wakes the orchestrator.

## Refinement drivers

After each MVP demo or dogfood run, update the spec from the evidence above:

- If a story passes only through a hand-crafted/scripted path, refine the story until the real user path is explicit.
- If a verifier cannot point to concrete evidence for a story, either add instrumentation/UI/tests or cut the story from MVP scope.
- If a story repeatedly needs human interpretation, promote the ambiguity into `mission.md`, `iteration.md`, or `wiki/` before more code is written.
- If implementation adds behavior not needed by a story, delete or defer it unless it directly protects reliability, security, or debuggability of the demo run.

**IN:** single-VM control plane (Firestore + GCS + Secret Manager), supervisor state machine, stateful orchestrator with cold-start fallback, Build mode with workstreams and serialized per-repo execution, multi-repo projects with spec-home repo, clarification inbox with batching and guess-propensity dial, verification gate with anti-bloat checklist, resource registry with observed-usage estimates and cooldown wake-ups, user's `gh` credentials, web UI (project list, project page, resources page), full episode logging with prompt versioning, VM-as-runner + laptop-as-runner, org-context document.

**OUT (post-MVP, roughly in order):** Maintain mode (fast follow), drift detection + `blocked: infra`, GEPA optimization loop, hive provisioning runners and injecting credentials from the vault, GitHub App + webhooks (heartbeat polling until then), notification channels beyond web UI, multi-user, within-repo parallelism via worktrees, finer-grained work-type toggles, automated provider-rulebook updates.
