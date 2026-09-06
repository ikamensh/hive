# UI redesign: implementation contract

Ilya selected concept 2 on 2026-09-06: a dark navigation sidebar, light working
surfaces, system typography, blue actions, and compact rows with clear dividers.
The redesign is implemented locally. This document describes the current code
and its intended behavior; it does not claim deployment or production validation.

## Product priorities

A two-minute visit first answers whether Hive is working, where work is
happening, and how much capacity remains. Recent results come next. Decisions
remain actionable without displacing that overview.

Starting work has two first-class paths: **Bring a plan** and **Design with AI**.
A high-level goal is valid design input. Hive uses the existing intake conversation
to inspect the project, develop the brief, and expose material ambiguity. Both
paths converge on an explicitly reviewed execution plan; approving a design brief
is separate from approving code execution.

Keep each surface focused. Use readable text, aligned rows, predictable
navigation, and explicit status labels. Dashboard previews have a count and a
route to the full collection. Avoid long parallel feeds and color-only meaning.

## Implemented navigation

| Surface | Route | Purpose |
| --- | --- | --- |
| Overview | `/` | Current execution, machine/agent/work mapping, spare capacity, recorded spend and recent landings. |
| Projects | `/projects` | Search name, goal or repository; see state reasons, landed/cancelled/remaining counts and an exact-plan next action. |
| Inbox | `/needs-you` | Search and page through questions, human tasks, plan approvals and blocked plan items; filter by kind and ownership. |
| Capacity | `/machines` | Machine status, usable agent choices, access checks and repair controls. Provider limits use `?tab=limits`. |
| Results | `/results` | Pageable recorded landings with validation evidence and links to the original plan/item. Opens from Overview. |
| Project | `/p/:id` | Work, Conversation, Activity, History, Issues and Tests tabs, selected with `?tab=…`. |
| Settings | `/settings` | Workspace configuration. Account controls also link to `/users`. |

Project settings and decision history remain at `/p/:id/settings` and
`/p/:id/decisions`. `?plan=<id>` selects a specific iteration; `&item=<id>` opens
its work-item detail. These links continue to identify the same evidence after
newer plans are created.

The project Work tab contains the selected plan, a bounded attention preview
and direct requests. Conversation owns intake and project questions. Activity
contains attempts and checkout diagnostics; History lists plans newest first.
Issues and Tests retain their existing pipelines and controls. There is no
permanent full-height question rail. See [project workflow](project-launchpad.md).

## Work entry and approval

**New work** is available from Overview, Projects and the project workspace.
Select an existing project or create one with a name, an existing repository
or a new private one, and an optional validation command. Recoverable setup failures retain
the created project ID so retry does not create another project.

- **Bring a plan:** continue to upload/paste on the Work tab. Preserve the source
  and submit with `start=false`; review the accepted revision before starting.
  This path does not start intake or require an internal planner.
- **Design with AI:** send the goal to intake and open Conversation. New projects
  retain the original text as `initial_spec`; an existing project's new goal is
  stored separately before the scout is queued. Same-goal retries reuse active
  intake. A different goal cannot replace an active conversation silently.

Conversation approval uses `action: approve`. When a new goal was discussed,
the agreed brief is finalized even if old spec files already exist. The UI
shows finalization and the wait for an actual plan proposal, including errors
and a retry action. The plan still needs execution approval. An already
executing plan must finish or stop before starting another design conversation.

## Status, capacity and evidence

Overview and Projects share the server's `/api/overview` snapshot. It includes
an observation time, pending/running work, runner/machine identity and heartbeat,
occupied/idle usable runners, and recent recorded landings. The browser does
not fetch every project and plan to reconstruct these summaries.

Overview distinguishes working, waiting, idle, paused and unavailable status.
Pause stops new dispatch while current tasks finish. A failed or old chief
observation makes current counts unknown; lost runner heartbeats remain visible
beside assigned work. Silence does not prove a laptop was locally paused.

Spare execution capacity counts usable unoccupied runners. Several agent CLIs
on one runner are alternatives, not extra parallel slots. Project grants,
capabilities and repository constraints can restrict otherwise idle capacity.

Provider windows show scope, remaining percentage, reset time and observation
age. Missing readings mean not reported. Old readings say last reported;
expired reset times say awaiting update. Account-wide quotas are not added
across machines. Recorded spend excludes unreported in-flight cost and remains
separate from subscription headroom and project caps.

Recent results require a recorded landed commit. Validation is successful only
when its receipt checks that commit; failed, absent and different-commit receipts
remain distinct. Completed attempts and completed plans alone do not establish
delivery or goal achievement. Results link to the exact plan and item.

## Current limits and possible later work

- Inbox currently aggregates persisted questions, human tasks, draft-plan/item
  approvals and parked plan items. Issue-specific blockers remain in Issues;
  intake brief review remains in Conversation. A complete cross-workstream
  attention projection would be a further change.
- The redesign reuses the existing intake/planner, issue and testing systems.
  It does not add a separate goal planner or merge their persisted work models.
- Checkout diagnostics are read-only. Checkout synchronization controls,
  inferred laptop pause state and estimated missing provider quotas are not
  implemented.
- Notifications and broader analytics can be considered later.
  They are not prerequisites or claims of this implementation.

## Verification contract

Exercise public HTTP behavior with local stores/scripted agents and browser
journeys for both work-entry paths, approval, execution, pause, attention and
evidence. Verify failed/stale responses, missing gauges, setup retries,
cancellations, exact-plan reloads and commit-specific validation. Browser
fixtures verify UI behavior; they do not replace execution integration tests.

Inspect desktop and mobile screenshots, including keyboard navigation and
primary controls. Keep the first viewport focused on working/where/capacity,
with bounded result previews. A local build or screenshot is not a deployment.

## Design references

- [Carbon dashboard guidance](https://carbondesignsystem.com/data-visualization/dashboards/)
- [GitHub workflow monitoring](https://docs.github.com/en/actions/how-tos/monitor-workflows)

Generated concepts and local screenshots are review artifacts, not product assets.
