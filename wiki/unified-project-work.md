# Unified project work and issue solving

Design target: Hive should have projects, not "spec projects" and "issues
projects". Issue solving is a workstream inside a project, alongside the normal
iteration-building workstream. The UI should make this feel like one project
with multiple ways to produce useful work.

This document records the workstream design proposal. Its domain rationale
remains useful; migration steps and proposed APIs below are design history,
not a statement that every proposal is implemented. The current UI follows
[UI redesign](ui-redesign.md) and [project workflow](project-launchpad.md).

## Product principle

The user-facing model is:

- A **project** is the durable container: mission, iteration, repos, policies,
  resources, attention queue, and activity.
- A project has one or more **workstreams**. The default workstream is the
  iteration goal. GitHub issue solving is another workstream attached to a repo.
- A workstream wraps a **source** of possible work, such as the project spec or
  GitHub issues, plus the executor and run policy Hive uses for it. Iteration
  work uses the LLM orchestrator. GitHub issue solving keeps the deterministic
  resolve/review/land state machine because that reliability is the point of
  the current implementation.

This intentionally does not mean "put GitHub issues through the general
orchestrator". The split to remove is the project identity split, not the
executor contract.

## Nomenclature

Target hierarchy:

```text
Workspace
  Project
    Workstream       # ongoing channel of project work
      work item      # durable unit of desired work
        Task         # one runner execution attempt
```

Supporting concepts:

- **Workspace** — account/org boundary. Owns projects, runners, machines,
  provider capacity, and org-wide context.
- **Project** — durable product context: mission, spec home, member repos,
  policies, budget, attention queue, and activity.
- **Spec home** — the repo containing project memory: `mission.md`,
  `iteration.md`, `wiki/`, and `input-log/`.
- **Source** — the raw upstream place work can come from: project spec,
  GitHub issues, later Linear tickets or scheduled checks. A source is not the
  Hive object; it is what a workstream wraps.
- **Workstream** — an ongoing channel of project work with a source, executor,
  repo scope, status, and policy. Examples: "iteration goal" and "GitHub issues
  for `ikamensh/hive`".
- **Work item** — one durable unit inside a workstream. Examples: an
  orchestrator-created build chunk like "auth flow", or GitHub issue #42.
- **Task** — one execution attempt assigned to a runner. It has exact
  instructions, backend/model, repo/branch, status, cost, result text, and
  trace. One work item may have many tasks: implement, verify, fix, resolve,
  review. Failed or rejected tasks do not replace the work item; they are its
  execution history.
- **Run** — a bounded user-triggered batch on a workstream. For issue solving,
  an `IssueRun` snapshots selected issue numbers and drives them one after
  another. Iteration work may not need explicit runs beyond "start/continue".
- **Machine** — a durable host the user recognizes; the user-facing capacity unit. A machine is either a cloud server expected to stay online or a personal computer that may sleep, move, or disconnect.
- **Runner** — a technical access link that can execute tasks on a machine. Runner is not a user-facing machine type, and normal operation has one runner per machine.
- **Resource** — a usable `(runner, backend)` capacity unit with auth,
  quota/cooldown, and cost accounting.
- **Question** — a clarification Hive asks the user because a decision would
  materially affect the work.
- **HumanTodo** — an action only the operator can perform outside Hive, such as
  refreshing a CLI login or fixing DNS. Clearer than `HumanTask`, which collides
  with runner tasks.
- **Inbox** — the UI attention queue (`/needs-you`). The current projection
  includes questions, human tasks, plan approvals and blocked plan items;
  issue-specific blockers retain their project view.

Names to change from the current code in this design:

| Current name | Target name | Why |
| --- | --- | --- |
| project source-mode field | removed | Projects should not be mutually exclusive source modes. |
| `HumanTask` | `HumanTodo` | It is an operator action, not an agent execution. |

Optional later renames if they still feel clearer after the workstream refactor:

| Current name | Possible name | Why |
| --- | --- | --- |
| `GuessPropensity` | `ClarificationPolicy` | It controls when Hive guesses versus asks. |
| `OrchestratorRun` | `PlannerInvocation` | It records one planner/orchestrator call and its cost. |

## Proposed concepts

### Project

`Project` stops carrying a source-mode field. It remains the top-level object:

- `name`
- `spec_repo`
- `member_repos`
- `mode`
- `autonomy`
- `guess_propensity`
- `prod_deploys`
- `paused`
- `daily_budget_usd`
- `goal_complete`
- `state`

`mode=build|maintain` is still a project policy. It should not decide where
work comes from.

### Workstream

Use `Workstream` for the thing above a source like GitHub issues:

```python
class WorkstreamKind(StrEnum):
    iteration = "iteration"
    github_issues = "github_issues"

class WorkstreamStatus(StrEnum):
    idle = "idle"
    active = "active"
    blocked = "blocked"
    disabled = "disabled"

class Workstream(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    kind: WorkstreamKind
    title: str
    repo: str = ""        # empty for the project-level iteration workstream
    source_ref: dict = {} # e.g. {"provider": "github", "issues": true}
    status: WorkstreamStatus = WorkstreamStatus.idle
    enabled: bool = True
    config: dict = {}
    created_at: float
    updated_at: float
```

Every project gets exactly one `iteration` workstream. A project can have zero
or more `github_issues` workstreams, usually one per member repo.

Implementation naming note: the current code already uses `Workstream` for the
smaller unit an orchestrator works on. Do not force a broad rename yet. The
product language can use lowercase "work item" for a GitHub issue or planner
chunk while the persisted models settle. The hierarchy is:

```text
Project
  Workstream     # iteration goal, GitHub issues for repo X
    work item    # auth-flow chunk, issue #42
      Task       # one runner execution attempt
```

Current implementation waypoint: the new top-level persisted object is named
`ProjectWorkstream` internally so the existing `workstreams` collection can keep
serving legacy work-item rows safely. The API already exposes the target shape:
`workstreams` are the top-level streams, while old per-unit rows are returned as
`work_items`.

### Work Items

Make workstream ownership explicit for the current smaller rows:

```python
workstream_id: str = ""
repo: str = ""
external_ref: dict = {}  # issue number, URL, labels, attachment names
```

Done: the smaller row is `IssueItem` (collection `workstreams` kept for data),
purely the GitHub-issue work item — the manual/`source` split died with the
legacy build path. The invariant holds: every issue row belongs to a GitHub
issues workstream and a concrete repo.

### IssueRun

Add a run/batch object. This solves the current live-validation pain where a
scan for issues #2-#4 also picked up a newly opened #5 and started it.

```python
class IssueRunStatus(StrEnum):
    scanning = "scanning"
    queued = "queued"
    running = "running"
    blocked = "blocked"
    done = "done"
    cancelled = "cancelled"
    failed = "failed"

class IssueRunScope(StrEnum):
    selected = "selected"
    all_open_now = "all_open_now"
    scan_only = "scan_only"

class IssueRun(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    workstream_id: str
    repo: str
    scope: IssueRunScope
    issue_numbers: list[int] = []
    status: IssueRunStatus
    started_at: float = 0
    finished_at: float = 0
    counts: dict = {}
```

`all_open_now` snapshots the issue numbers at trigger time. New issues found
later are synced into the workstream, but they do not join that run unless the
user starts another run. Continuous/watch mode can be added later as a different
run policy.

`Task` should eventually distinguish the parent workstream, the durable item
being executed, and the optional run. During migration, the existing
`Task.workstream_id` keeps its current smaller-row meaning and `work_item_id`
acts as a bridge for issue-run wiring. Issue `resolve`, `review`, and
`preflight` tasks set the bridge fields. Iteration `work` and `verify` tasks
keep the existing shape until the data model can be simplified without churn.

## Issue workstream behavior

The existing issue pipeline remains the core:

1. User triggers a run on a GitHub issues workstream.
2. Hive preflights the selected repo.
3. Hive fetches open GitHub issues with comments and embedded images.
4. Hive reconciles them into `github_issue` work items for that workstream.
5. If the run is not `scan_only`, Hive starts the lowest-order selected issue.
6. Resolve task clarifies and fixes in one warm session.
7. Review task uses a fresh session, may amend, then accepts or rejects.
8. Accept means merge branch into the repo default branch and close the issue.
9. Reject or blocked means the workstream needs attention, with the agent's
   GitHub comment as the human-facing explanation.

The strict sequencing invariant becomes: at most one issue per GitHub issues
workstream is in `resolving` or `reviewing`. Different repos may run issue
workstreams concurrently, subject to the existing one-task-per-repo dispatch
rule.

The existing functions move from project-wide to workstream/run-aware:

- `preflight_checks(store, config, project, repo)`
- `create_preflight_task(..., workstream_id, repo)`
- `reconcile(store, project, workstream, issues)`
- `advance_issues(store, project, workstream, run)`
- `create_review_task(..., workstream_id, run_id)`

`scan-issues` no longer requires a project source mode. It is a
compatibility wrapper over the workstream/run endpoints.

## Interaction with iteration work

Issue solving and iteration building share the same project, attention queue,
activity feed, budget, resource pool, and repo serialization.

The deterministic issue executor may queue tasks without waking the
orchestrator. The orchestrator still handles iteration work and sees issue
workstream state in its snapshot so it does not mark the iteration complete
while a project-level blocker is unresolved. `mark_goal_complete` should check
only iteration work items; issue workstream completion is reported as run
completion, not iteration completion.

When an issue run is active on the same repo as iteration work, the existing
dispatcher serialization prevents conflicts. The project is simply `working`
while any workstream has pending/running work. If an issue workstream is blocked
but the iteration workstream has active work, the project can keep working; the
UI still shows the issue blocker in `Needs you`.

## Project state

Long term, project state should be computed from facts plus attention counts:

- `intake`
- `working`
- `needs_attention`
- `blocked_resources`
- `blocked_budget`
- `idle`
- `idle_goal_complete`

The UI can display the reason text: "needs answers", "2 issue blockers",
"runner login needed", "goal complete", etc. This is clearer than encoding
issue-specific terminal states on the project.

Issue work items still use `blocked_clarity` for their own lifecycle, but the
project-level state should surface the generic `needs_attention` condition.
Issue-queue terminal states live on issue workstreams/runs rather than project
identities.

## API shape

Suggested API:

```text
GET  /api/projects/{project_id}
     returns project, workstreams, work_items, tasks, questions, human_todos, runs

POST /api/projects/{project_id}/workstreams
     create an issue workstream for a member repo

PATCH /api/projects/{project_id}/workstreams/{workstream_id}
     enable/disable workstream, update config

POST /api/projects/{project_id}/workstreams/{workstream_id}/preflight
     run chief checks and optionally queue runner self-check

POST /api/projects/{project_id}/workstreams/{workstream_id}/sync
     fetch and reconcile GitHub issues without starting work

POST /api/projects/{project_id}/workstreams/{workstream_id}/issue-runs
     body: { scope, issue_numbers?, backend?, model? }
     sync, snapshot scope, and start deterministic issue solving

POST /api/issue-runs/{run_id}/cancel
     cancel queued issue work for that run
```

Backwards compatibility:

- Older clients that still send the legacy source-mode field are ignored by the patch
  model; issue workstreams are created from repo context instead.
- `POST /scan-issues` maps to "create or find issue workstream for `spec_repo`,
  run all_open_now".
- `POST /issues-preflight` maps to the issue workstream preflight.

## Current UI (2026-09-06)

Project creation has no source-mode toggle. **New work** offers a prepared-plan
handoff or AI-assisted goal design, for either a new or existing project. These
are entry paths into the same durable project, not separate project types.

The project workspace has **Work**, **Conversation**, **Activity**, **History**,
**Issues** and **Tests** tabs. Issues retains repository selection, preflight,
sync, scoped runs and its issue table; Tests retains testing-workstream tools.
Both share the project's budgets and resource policy. Settings and decisions
have dedicated routes. Activity and checkout diagnostics are secondary to work.

The former proposed permanent Needs you rail is replaced by a bounded attention
preview and a workspace **Inbox**. The current Inbox projection covers questions,
human tasks, plan approvals and parked plan items. Issue blockers remain in
Issues and intake review in Conversation; adding them to the complete workspace
projection is a separate follow-up.

The project portfolio shows each current/latest plan goal, state reason, separate
landed/cancelled/remaining counts and an exact-plan action. Fleet activity and
capacity have their own Overview and Capacity pages. See the linked current UI
documents for routes, evidence semantics and work-entry behavior.

## Migration

1. Create an iteration workstream for every project.
2. Attach the current smaller `Workstream` rows to their parent workstream.
3. For each legacy project that had issue work items, create a GitHub issues
   workstream for `project.spec_repo`.
4. Attach existing issue work items and resolve/review/preflight tasks to that
   workstream. If no run record exists, create a synthetic historical run.
5. Keep current issue statuses, but compute project state from all workstreams.
6. Remove the UI work-source picker.
7. After compatibility routes have no callers, delete remaining issue-specific
   project states and stale issue-solving docs.

## Test plan

- Unit: workstream creation, migration from old issue projects, preflight
  independent of project mode, issue run scoping, and
  one-active-issue-per-workstream invariant.
- API: create project, add issue workstream, sync, run selected issues, cancel
  run, retry blocked issue after a GitHub comment.
- Supervisor: issue tasks and iteration tasks share repo serialization and
  budget/resource blocking without suppressing unrelated ready work.
- Orchestrator: `mark_goal_complete` ignores issue workstream terminal work items
  but refuses completion while iteration work is unfinished.
- UI: mock project with both workstreams; verify project list, issues table, run
  controls, project attention and activity at desktop and mobile widths.

## Recommended implementation order

1. Add the parent workstream layer, `IssueRun`, and bridge fields while keeping
   existing fields and routes.
2. Move issue preflight/reconcile/advance to workstream-aware functions.
3. Replace setup/source mode with issue workstreams in the API and UI.
4. Rework project state computation to aggregate workstreams.
5. Add run scoping and selected-issue UI.
6. Delete compatibility code and stale issue-solving docs once existing data is
   migrated.
