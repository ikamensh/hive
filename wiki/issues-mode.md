# Issues mode

A project work source where Hive resolves a repo's open GitHub issues instead of decomposing a human-written iteration goal. Selected per project via `Project.work_source` (`spec` | `issues`). Spec mode is unchanged; issues mode swaps *where work comes from* and runs a simpler, mostly-deterministic per-issue pipeline.

**Principle: one issue, one warm session, independent review, nothing bad lands.** Each issue is clarified and fixed in a single agent session (context stays warm), reviewed by a fresh independent agent that may fix on the spot, and only merged on accept. Rejected or unclear work never reaches the default branch and always leaves a GitHub comment explaining why.

## The loop (deterministic; no orchestrator LLM in the path)

Driven by the human scan + the task-result state machine — not the planner. The orchestrator's ordering/solve tooling (`order_issues`, `resolve_issue`, `prompts/orchestrator_issues.md`) is kept in the tree, dormant, for a future ordered variant; it is not on the active path.

1. **Scan** (human-clicked from the UI or `POST /api/projects/{id}/scan-issues`; no automatic polling). For each open issue fetch **title, body, all comment text, and embedded image attachments**. Reconcile into one issue-workstream per issue (ingest new as `queued`, cancel externally-closed, re-queue previously-blocked/rejected). Then **strictly one issue at a time** (`advance_issues`): promote the lowest-numbered queued issue and create its **resolve task**; the next issue starts only once this one lands or parks.
2. **Resolve task** — configured issue backend (default `codex`) with `HIVE_ISSUE_MODEL` optional; if unset the backend chooses its current default. One session, on a per-issue branch `hive/issue-<number>`:
   - **Clarify first**: classify bug vs feature and judge buildability. Bug reports are doable by default (the work is investigate → reproduce → fix). Two BLOCKED paths — both post a GitHub comment (via `gh`), make **no code change**, and end `OUTCOME: BLOCKED`: (a) a feature needing an expensive-to-reverse product/behavior decision (comment lists what must be decided); (b) a bug the agent **cannot reproduce** on the working branch — the referenced UI/behavior/code doesn't exist here (a reporter screenshot is not proof it's on this branch; it may have been filed against unpushed/local work). The agent must never invent or reconstruct a missing element; it comments what it couldn't find and on which branch.
   - **Fix in the same session** if clear: implement the fix, commit on the branch, push. Ends `OUTCOME: FIXED`. Reusing the session means the fix inherits the clarification's context for free — this is why clarify and fix are *not* split into two tasks (kodo session resume exists but is machine-local; one task avoids pinning and persistence).
3. **Review task** — same backend/model as the resolve task, **fresh independent session**, on the same branch:
   - Review the fix against the issue. **Fix small problems on the spot** (commit + push to the branch) — no back-and-forth with the fixer.
   - Decide `REVIEW: ACCEPT` (the fix, possibly amended, is good) or `REVIEW: REJECT` (major flaws, or it makes other areas worse and can't be salvaged here). On reject the agent posts a GitHub comment stating **what went wrong and the recommended approach for the next attempt**.
4. **Land** (Hive, deterministic, no PR workflow):
   - **ACCEPT** → merge `hive/issue-<number>` into the default branch via the GitHub **merges API** (`POST /repos/{owner}/{repo}/merges`, no PR object), then close the issue with a summary comment. The close step is idempotent: if GitHub already reports the issue closed (for example because a merged commit auto-closed it), Hive treats that as success. The branch is **kept** (review/debug history).
   - **REJECT** → leave the default branch untouched; the reviewer's comment already explains the failure. Branch kept.
   - **Landing failure** → create a `HumanTask` and do **not** advance to the next queued issue until the landing failure is resolved. The UI's human-task "mark done" action verifies the GitHub issue is closed and marks the issue-workstream `done`.

No ordering: issues are processed independently. Per-repo serialization still holds (one task per repo at a time), so for a single spec repo the pipeline runs issue-by-issue in practice.

## Running an agent: the workspace

A task's only inputs are a repo checkout and a text instruction string (`hive/runner.py: execute` → `kodo` `CodexSession` → `Agent.run(instructions, project_dir)`). Issues mode enriches the checkout:

- Repo checked out to the per-issue branch.
- Resolve retries are intentionally fresh: if `origin/hive/issue-<n>` already exists, the runner preserves it as `hive/issue-<n>-previous-<timestamp>`, resets `hive/issue-<n>` from the latest default branch, and force-with-lease pushes that reset before the agent starts. Local dirty checkout state is reset/cleaned before branch switching. This keeps retries from building on stale rejected attempts while preserving the old branch for debugging.
- Issue context written into the working tree (and added to `.git/info/exclude` so the agent doesn't commit it): `.hive/issue-<n>/ISSUE.md` (title + body + every comment) and `.hive/issue-<n>/attachments/*` (downloaded embedded images). The instructions point the agent at this folder.

### Images — capability to validate

kodo's codex session is **text-only** (`query(prompt: str, ...)`); there is no image-input path. Decision: **place image files in the workspace and mention them to the agent**, then empirically test which backends can actually inspect them (OCR vs full graphical understanding) and record the result here. Until validated, assume the agent may not see images; do not depend on image content for correctness.

## Data model

- `Project.work_source: WorkSource` = `spec` | `issues` (done).
- `Workstream` (issue-workstream): `source=issue`, `issue_number`, `issue_url`, `branch = hive/issue-<n>`, `order` (= issue number; lowest goes first).
- **Strict per-issue sequencing** (`advance_issues`): at most one issue is in flight (`resolving`/`reviewing`) at a time. When none is, the lowest-`order` `queued` issue is promoted to `resolving` and its resolve task queued; called after every scan and every landing. This means each issue branches from a default branch that already includes prior landed fixes — issues that touch the same files can't conflict on the second merge.
- `Task.fresh_branch` is set on resolve tasks (not review tasks) so retries reset the active issue branch from default while preserving previous attempts under timestamped branch names.
- Issue-workstream lifecycle (status):
  - `queued` — ingested/awaiting its turn (no task yet).
  - `resolving` — resolve task pending/running.
  - `blocked_clarity` — resolve returned BLOCKED; agent commented on the issue. Awaits human clarification.
  - `reviewing` — review task pending/running.
  - `rejected` — review returned REJECT; agent commented with what went wrong + next approach.
  - `done` — accepted, merged into default, issue closed.
  - `cancelled` — issue closed on GitHub by a human.
  - Re-scan re-queues any still-open issue that isn't `done` and has no live task (`blocked_clarity`/`rejected`/reopened `cancelled`/errored-mid-flight) so a clarified/reopened issue is retried in order.
- `TaskKind`: `resolve`, `review` (replacing the interim `clarity` task kind). Result markers parsed deterministically (mirroring `parse_verdict`): resolve → `OUTCOME: BLOCKED|FIXED`; review → `REVIEW: ACCEPT|REJECT`.
- `ProjectState`: `working` (a task pending/running), `blocked_clarity` (open issues remain but all are `blocked_clarity`/`rejected` — waiting on the human), `idle_no_open_issues` (queue drained).

## Who does what on GitHub

- **Agents** (via `gh` on the runner, using the runner's GitHub auth): post the *qualitative* comments — the clarity-blocked questions and the rejection rationale. These are "from the agent."
- **Hive control plane** (via the GitHub API with `config.gh_token`): the *mechanical* steps — fetch issues/comments/attachments, merge the branch on accept, close the issue. No PRs are created.

## Build status

Backend pipeline is built and unit/e2e-tested (`tests/test_issues.py`):
1. ✅ `fetch_open_issues_full` (issues + comments + embedded image URLs); runner `prepare_issue_workspace` materializes `.hive/issue-<n>/` (ISSUE.md + downloaded attachments, git-excluded).
2. ✅ `resolve` task kind + `prompts/resolve.md` (clarify→fix, codex `gpt-5.5`, branch `hive/issue-<n>`); `parse_resolve` (`OUTCOME: FIXED|BLOCKED`).
3. ✅ `review` task kind + `prompts/review.md` (fresh codex `gpt-5.5`, fix-on-spot, rejection comment); `parse_review` (`REVIEW: ACCEPT|REJECT`).
4. ✅ Deterministic state machine in `api.task_result` (`_land_resolve`/`_land_review`): resolve→review chaining, `merge_branch` (merges API) + idempotent `resolve_issue_on_github` on accept, escalate and stop advancement on unresolved landing failure; `compute_state` + supervisor skip the planner for issues projects.
5. ✅ Strict per-issue sequencing (`advance_issues`): one issue through resolve→review→land before the next starts. Resolve retries get a fresh branch from current default while preserving the old attempt. Interim batch `clarity` task removed (folded into resolve); the spec-mode ordering code (`activate_next`, `order_issues`, `resolve_issue`, `orchestrator_issues.md`) kept dormant.

6. ✅ Preflight gate (`hive/preflight.py`): control-plane checks + a runner self-check (push + gh auth); `scan-issues` is gated on the hard checks.
7. ✅ UI pass: `work_source` toggle, preflight + Scan buttons, structured preflight/scan errors, issue list grouped by lifecycle state with links to issue + branch, task cancel button, and human-task completion for accepted-but-not-yet-marked-done issue lands (`web/`).

## Live validation notes (2026-06-14)

- Ran the issues project fully from the UI against `ikamensh/hive`; issues #2, #3, and #4 reached `done` and are closed upstream as `COMPLETED`.
- A new upstream issue #5 appeared during the scan. Because the validation target was #2-#4, its auto-start was cancelled from the UI and it remains queued for a later intentional run. Product decision to revisit: whether issue-mode scan should offer a selected subset/run boundary or always process every open issue.
- Issue #4 exposed GitHub close idempotency: the fix had landed and GitHub reported the issue closed, but Hive treated the close PATCH's 422 as a failed land. The close helper now verifies closed state after a failed close, and the landing human-task path can mark the matching workstream done from the UI.

Image attachments are downloaded **on the control plane** (authed with `gh_token`) at scan and served to the runner, so the worker needs no GitHub creds; `attachments_failed` in the scan result flags any that didn't resolve.

## Preflight (checked preconditions before a big run)

A real run depends on things the happy-path code can't see; `hive/preflight.py` turns them into checks so misconfiguration surfaces up front, not as a half-finished pipeline. `hive preflight <project>` (→ `POST /api/projects/{id}/issues-preflight`) reports:

- **Control-plane checks** (one GitHub GET): `issues_mode`, `spec_repo_set`, `gh_token_present`, `repo_write_access` (the token's `permissions.push` — needed for merge-on-accept + issue close), and the soft `issues_enabled` / `codex_runner_usable`.
- **Runner self-check** (`TaskKind.preflight`, run by `runner.run_preflight` on the codex runner against the real repo): pushes a throwaway branch and deletes it (proves `git push` auth) and runs `gh auth status` (proves the agent can comment). These are the agent-facing risks the control plane can't verify itself.

`scan-issues` re-runs the control-plane checks and refuses (409) if a hard one fails. The scan response also reports `attachments_downloaded` / `attachments_failed` so image-fetch problems are visible on the run itself.

## Open questions / to validate

- Image inspection per backend (OCR vs graphical) — test and record. (Image *fetch* now happens control-plane-side at scan and is counted in the scan result; inspection capability is still per-backend.)
- Merge conflicts on the merges API (should be rare under per-repo serialization since the branch is cut from current default) — on failure, mark blocked/escalate rather than force.
- Reusing the dormant ordering logic when an ordered variant is wanted.
