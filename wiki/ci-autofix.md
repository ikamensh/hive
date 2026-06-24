# CI auto-fix

Hive watches each project repo's default-branch CI and, when the build goes red,
**files a GitHub issue and lets the existing issue-solving pipeline fix it**. It
is the testing-workstream pattern (`wiki/testing.md`) applied to CI: a
deterministic check *produces a GitHub issue*; the resolve→review→land machine in
`wiki/issue-solving.md` does the actual fixing. There is no separate CI fixer.

## Why this shape

The architecture already calls out "a red main build (when CI exists) is an event
that triggers a fix task" (`wiki/architecture.md` §4). Splitting it into
*issue creation* + *reuse of the issue pipeline* keeps one trustworthy code path
doing the fixing and makes the whole thing a thin, testable add-on.

## The toggle

`Project.ci_autofix` (default off) is the per-project switch, shown in the policy
grid next to *prod deploys* and *paused*. When on, all three triggers below run
the same check; the toggle is the single "Hive reacts to red CI for this project"
control.

## Triggers (all run the same check)

1. **Webhook (real-time).** A repo's GitHub Actions workflow forwards a CI
   failure to `POST /api/ci/webhook`, bearer-authed by
   `HIVE_GITHUB_WEBHOOK_SECRET`. This is the low-latency path: Hive files the
   issue seconds after the build goes red. The forwarder template is
   `deploy/ci-autofix.github-workflow.yml` (copy it into a repo as
   `.github/workflows/hive-ci-autofix.yml` and set the `HIVE_URL` +
   `HIVE_GITHUB_WEBHOOK_SECRET` repo secrets). It posts `{repo, ref, event:
   ci_failure, run_id, details}` where `details` is the tail of the failing-run
   logs — embedded into the issue so the resolve agent has them. Modelled on the
   `ci-assistant` VPS service (`~/ai-workspace/vps/ci-assistant`). The endpoint
   re-confirms with `fetch_ci_status`, so a feature-branch failure that left the
   default branch green is a no-op.
2. **Poll (safety net).** With the toggle on, the supervisor polls each repo
   (spec home + members) every `Supervisor.CI_CHECK_INTERVAL_S` (5 min) — catches
   anything the webhook missed (workflow not installed, delivery failure).
3. **Manual.** A "check CI" button on the Issues toolbar, `POST
   /api/projects/{id}/workstreams/{ws}/check-ci`, and `hive check-ci`.

All three are idempotent through the same per-sha dedup, so overlapping triggers
never double-file.

## The check (`hive/workstreams/_ci.py`)

`fetch_ci_status(repo, token)` reads the default branch's head commit and combines
the GitHub **check-runs** API (Actions and other check apps) with the legacy
**commit-status** API into one verdict: `failing` if anything failed, `pending`
if some are still running and none failed, `passing` if there are checks and all
succeeded, `none` if the branch has no CI at all (so repos without CI never get
noise).

`check_and_autofix(store, project, workstream, token, ...)` is the orchestration
(pure store ops; the only network is the two GitHub functions):

1. Get the CI verdict. If not `failing`, do nothing — no store writes, no issue.
2. **Dedup.** Each CI issue body embeds `<!-- hive-ci sha=<sha> -->`. If an open
   issue already carries the marker for this commit, reuse it (`already_filed`)
   instead of filing again — a repeated check of the same red commit never opens
   a duplicate. A *new* red commit (new sha) files a fresh issue.
3. **File** the issue (label `hive-ci`) when there is no match.
4. `reconcile` the workstream against the repo's open issues (so the CI issue
   becomes a `queued` issue work item) and `advance_issues` to queue the resolve
   task — the same one-issue-at-a-time pipeline that fixes human-filed issues.

From there it is ordinary issue solving: resolve on `hive/issue-<n>`, independent
review, then merge into the default branch + close. The merge re-triggers CI; a
later check confirms green (or files the next failure for a new commit).

## Tests

`tests/test_ci.py`: status parsing across check-run/commit-status shapes; a red
build files an issue and queues a resolve on its branch (the pipeline-reuse
property); the same red commit is not refiled; a green build touches nothing;
the supervisor gate respects the toggle and interval; the toggle persists via the
API.
