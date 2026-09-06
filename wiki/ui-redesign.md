# UI redesign brief

Confirmed with Ilya on 2026-09-06. This describes the intended redesign;
implementation has not started and the visual direction is being selected.

## Product priorities

A two-minute visit must first make clear whether Hive is working, where the
work is happening, and how much capacity it has. Recent results come second.
Attention and decisions remain actionable without displacing that overview.

Starting work has two first-class paths: bring an elaborate plan, or develop
one with AI. A high-level goal is valid input. Hive should inspect the project,
make useful progress on defining the work, expose material ambiguity, and ask
focused questions when needed. Use the existing intake and plan pipeline.

The style must be clear and focused, with a defined purpose for each area and
familiar application conventions. Prioritize readable typography, aligned
rows, predictable navigation, and explicit status labels. Keep dashboard
previews bounded, with a total and a link to the full collection.

## Information architecture

- **Overview:** current execution, project/agent/machine mapping, capacity and
  recent delivered outcomes. A compact attention summary links to the Inbox.
- **Projects:** find a project and open its work, plan history, evidence,
  conversation, and settings. Selected work opens a consistent detail surface.
- **Inbox:** complete, filterable decisions, approvals, blocked work and human
  tasks, with the relevant action and original context.
- **Capacity:** machines, usable agents, provider limits, and repair actions.
- **Settings:** workspace administration, users, storage and account controls.

One **New work** entry offers prepared-plan submission and AI-assisted design.
Both converge on plan review, execution and evidence. Broad goal exploration
does not silently approve code execution; existing execution controls remain.

## Data and behavior

The overview should use one server snapshot with an observation time. Extend
the existing overview rather than fetch every task, project and plan from the
browser. Include useful work titles, runner/machine identity and heartbeat,
pending work, occupied/idle usable runners, and recent recorded landings.

Distinguish Working, Waiting, Idle, Paused, and Paused while current tasks
finish. A lost chief connection means current status is unknown. A lost runner
heartbeat must be visible beside its assigned work. Do not infer a laptop's
local pause state from its absence.

Count spare execution capacity by usable unoccupied runners. Several installed
or usable agent CLIs on one runner are alternative choices, not extra parallel
slots. Project grants, capabilities and repository constraints can still make
an otherwise idle runner unsuitable for particular work.

Render existing provider usage windows with scope, remaining percentage, reset
time and observation age. Missing gauges mean not reported. Do not combine
account-wide quotas across machines or present an expired gauge as fresh.
Recorded spend today is separate from subscription headroom and project caps;
it excludes unreported in-flight cost.

Recent delivered outcomes require a recorded landed commit. Show validation
success only when evidence applies to that commit. Task completion and plan
completion alone do not prove delivery or goal achievement. Preserve exact-plan
links across later iterations.

## Implementation sequence

1. Select the overview layout from three restrained visual concepts.
2. Extend overview/capacity types and server aggregation; verify status,
   resource accounting and delivery semantics with integration tests.
3. Replace the application shell and overview, then the project workspace,
   complete Inbox, Capacity and secondary settings surfaces.
4. Connect both New work paths. Restore goal input through `spec_text`, start
   intake for AI design, and use conversation `action: approve` when approval
   should queue missing spec-file finalization. Keep planning/wait/failure
   state visible until an actual proposal arrives. Retain imported-plan
   submission and its duplicate/error-recovery contract.
5. Add complete Inbox and plan-history listing APIs where needed. Reuse
   persisted execution models; a coherent UI does not require replacing them.
6. Update current workflow docs with implemented behavior, delete superseded
   UI, review simplifications, and commit stable increments.

## Acceptance

Exercise the real HTTP contract with local stores and scripted agents, plus
browser journeys for both work-entry paths, approval, active execution,
pause/drain, waiting for capacity, resolving attention and inspecting delivery.
Verify unknown/stale data, missing quota gauges and failed requests explicitly.
Retain artifact submission, exact-plan reload, cancellation and evidence tests.

Inspect screenshots at desktop and mobile sizes. The first viewport must
answer whether Hive is working, which projects/machines are involved, and
what capacity remains. Navigation and primary controls must work by keyboard.
Avoid horizontal overflow, long dashboard feeds and color-only status meaning.

## Design references

- [Carbon dashboard guidance](https://carbondesignsystem.com/data-visualization/dashboards/):
  prioritize operational information and make detail accessible through drilldown.
- [GitHub workflow monitoring](https://docs.github.com/en/actions/how-tos/monitor-workflows):
  familiar run lists, execution detail and evidence navigation.
- Existing local screenshots captured during review:
  `/tmp/hive-redesign-review/01-home.png`, `02-project.png`, `03-inbox.png`.
  These are temporary review artifacts, not committed product assets.
