# Project workspace: submit, monitor, inspect

The project page centers on one handoff: prepare an agreed plan, let Hive execute
it, and inspect the delivered changes or the decision that stopped progress.
Planning with Hive is optional; imported plans use the same execution pipeline.
See [artifact workflow](artifact-workflow.md) for the shared CLI/API contract.

## Primary flow

1. **Connect a project.** Home → New project asks for a name, an existing or new
   repository, and an optional validation command. Creation does not start an
   intake scout. A failed setup retains the created project ID for retry.
2. **Submit.** Upload or paste a TOML/Markdown plan. The browser preserves the
   source and sends it to `POST /api/projects/:id/plan/submit` with `start=false`.
   The chief parses it, retains the accepted revision, and handles duplicates.
   Errors stay beside the editable source. Submission is not approval.
3. **Review and start.** Inspect and edit the accepted items, check the validation
   command, and approve the plan. A configured project can use this path without
   intake, existing tasks, or a planner API key.
4. **Monitor.** The ordered work list shows building, review, landed, cancelled,
   and blocked states with reasons. Retry and cancellation stay on the item.
   The project inbox sits alongside the plan on desktop and below it on mobile.
5. **Inspect.** Results & evidence reads `/api/plans/:id/result`. When the plan
   finishes or is abandoned, this becomes the default view. It distinguishes
   execution ending from goal achievement, preserves cancellations, and shows
   item commits, attempt reports, review verdicts, validation command/output and
   the checked commit. The downloaded JSON is the complete captured response.

Every accepted submission selects `?plan=<id>`. “Link to this plan” preserves
that identity across reloads and newer plans. “Back to current plan” returns to
the project's latest plan. Result-fetch failures are visible, including when a
previous snapshot remains on screen.

## Amendments and validation

Export plan downloads the canonical TOML. Edit eligible future work while
preserving its revision and item keys, then use “Amend from a plan file”. The
server's revision checks and approval requirements apply unchanged.

“Validation before landing” shows the current project command and lets the
operator edit it. Changes apply to newly queued reviews. A blank command means
agent review only; the result view never invents a successful validation run.
Completed execution and abandoned execution both retain their evidence and
provide a secondary “Start the next plan” disclosure.

## Secondary tools

- “Other ways to prepare a plan” offers Hive drafting and a handwritten plan.
- “Let Hive help prepare the specification” retains optional intake setup for
  configured projects that have not yet supplied a plan or completed intake.
- “Other work” contains direct requests, issue runs and testing episodes. These
  continue using their existing pipelines; this iteration does not merge their
  persisted work models into plans.
- “Diagnostics” contains agent activity and machine checkout facts. It fetches
  machine metadata only when opened. Checkout drift is informational; no sync
  action is offered because the sync agent is not implemented.
- Settings and Decisions retain their dedicated pages.

## Verification

`web/tests/plan-workflow.spec.js` exercises the real page and HTTP client using
API fixtures. It covers rejected submissions retaining source, approval without
intake, setup retries without duplicate projects, automatic completion updates,
cancelled items, checked/landed commit evidence, downloads, saved plan links,
result-fetch errors, and a 390px result layout. Set `HIVE_UI_SCREENSHOTS` to save
full-page captures while running those tests.

Backend artifact/result/wait tests and the local Git artifact demo verify the
underlying contract without live agents or production GitHub changes. Mock UI
mode only previews saved plans; submission and canonical export need a chief.
