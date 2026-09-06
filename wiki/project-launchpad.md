# Project workspace: design, submit, monitor, inspect

A project can begin with prepared work or a goal developed with Hive. Both paths
lead to an agreed plan, explicit execution approval, and recorded results. The
shared CLI/API handoff is documented in [artifact workflow](artifact-workflow.md);
the current local UI design is in [UI redesign](ui-redesign.md).

## Start work

Choose **New work** from Overview, Projects or a project. Select an existing
project or create one with a name, repository and optional validation command.
New repositories are private. A recoverable setup failure retains the created
project ID and already completed repository creation for retry.

**Bring a plan** opens the project's Work tab for upload or paste. It does not
start intake. The browser sends the intact TOML/Markdown source to
`POST /api/projects/:id/plan/submit` with `start=false`. The chief retains the
accepted revision and handles duplicates; errors stay beside the editable source.
Inspect the accepted items and validation command, then approve execution. A
configured project needs neither a scout nor a planner API key for this path.

**Design with AI** accepts a concrete or broad goal and opens Conversation. Hive
uses the existing intake scout to inspect repository context, prepare a brief
and surface assumptions or questions. New-project input is retained as the
original spec; subsequent goals are stored separately before work is queued.
Same-goal retries reuse active intake. Finish or stop an executing plan before
starting another design conversation.

Answer or correct the brief in Conversation, then approve it. Approval uses
`POST /api/conversations/:id/message` with `action: approve`; a newly discussed
goal is finalized into the spec even if older spec files exist. Finalization and
waiting for a proposal remain visible, with errors and a planning retry action.
Approving the brief does not approve code execution: review the resulting plan
on Work and start it explicitly.

## Workspace navigation

`/p/:id` defaults to **Work**. Tabs use `?tab=work|conversation|activity|history|issues|tests`.

- **Work:** ordered plan items, execution readiness, validation command, approval,
  retry/cancel controls and Results & evidence. A top-three attention preview
  links to the workspace Inbox. Direct requests remain in a disclosure.
- **Conversation:** intake, questions, human tasks and answered-question history.
- **Activity:** execution attempts with a bounded initial list and Show more;
  checkout/machine diagnostics are in a disclosure and are read-only.
- **History:** plans newest first, each linking to its own execution and evidence.
- **Issues / Tests:** repository-scoped workstream controls and their existing
  pipelines. These are work inside the same project, not separate project types.

Project pause/resume is explicit in the header. Settings and decision history
retain dedicated routes: `/p/:id/settings` and `/p/:id/decisions`. The workspace
Inbox at `/needs-you` currently covers questions, human tasks, plan approvals and
parked plan items; issue blockers and intake review remain on their project tabs.

Projects (`/projects`) shows the current/latest goal, state reason and separate
landed, cancelled and remaining counts. Its next action opens the exact plan.
The Overview (`/`) instead prioritizes execution location, capacity and recent
landings; the complete delivered-results collection is at `/results`.

## Execution, results and amendments

Execution readiness previews builder/reviewer capacity, model quotas, allowances,
pause and budget. Approval can queue work that must wait; the preview reserves
nothing and does not prove repository permissions or successful validation.
The work list shows building, review, landed, cancelled and blocked states with
reasons. Retry and cancellation remain on the item.

**Results & evidence** reads `/api/plans/:id/result` and becomes the default plan
view after execution finishes or is abandoned. It distinguishes execution ending
from goal achievement, preserves cancellations, and shows item commits, attempt
reports, review verdicts, validation command/output and the checked commit.
Download result JSON preserves the complete captured response.

Every accepted submission selects `?plan=<id>`. **Link to this plan**, History,
Projects and recent results preserve that identity across reloads and later
iterations. `&item=<id>` opens a specific item. **Back to current plan** returns
to the latest plan. Failed evidence requests remain visible even when an earlier
snapshot is retained.

**Export plan** downloads canonical TOML. Edit eligible future work with its
revision/item keys, then use **Amend from a plan file**. Existing revision checks
and approval requirements apply. The validation command can be edited on Work;
changes apply to newly queued reviews. Blank means agent review only, never an
invented successful validation run. Ended plans retain evidence and the
secondary **Start the next plan** disclosure.

## Verification

`web/tests/work-entry.spec.js` covers the two entry paths, setup retry and goal
preservation, conversation approval, and mobile work entry.
`web/tests/plan-workflow.spec.js` covers source recovery, approval, completion,
cancellation, commit-specific evidence, downloads and saved plan links.
`web/tests/home-plans.spec.js` covers portfolio progress and exact-plan navigation.
Set `HIVE_UI_SCREENSHOTS` to capture supported browser journeys.

`tests/test_intake_goal.py` verifies goal persistence, duplicate retries and
finalizing a new brief over existing spec files through the public API. Backend
artifact/result tests and the local Git demo cover the execution contract without
live agents or production GitHub changes. Mock mode is for saved-state previews;
submission and canonical export require a chief. None of these checks deploys the UI.
