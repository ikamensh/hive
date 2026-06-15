# Unified project work and issue solving

Design target: Hive should have projects, not "spec projects" and "issues
projects". Issue solving is a workstream inside a project, alongside the normal
iteration-building workstream. The UI should make this feel like one project
with multiple ways to produce useful work.

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
      WorkItem       # durable unit of desired work
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
- **WorkItem** — one durable unit inside a workstream. Examples: an
  orchestrator-created build chunk like "auth flow", or GitHub issue #42.
- **Task** — one execution attempt assigned to a runner. It has exact
  instructions, backend/model, repo/branch, status, cost, result text, and
  trace. A work item may have many tasks: implement, verify, fix, resolve,
  review. Failed or rejected tasks do not replace the work item; they are its
  execution history.
- **Run** — a bounded user-triggered batch on a workstream. For issue solving,
  an `IssueRun` snapshots selected issue numbers and drives them one after
  another. Iteration work may not need explicit runs beyond "start/continue".
- **Runner** — a process that can execute tasks on a machine.
- **Machine** — a durable host the user recognizes; runners come and go on it.
- **Resource** — a usable `(runner, backend)` capacity unit with auth,
  quota/cooldown, and cost accounting.
- **Question** — a clarification Hive asks the user because a decision would
  materially affect the work.
- **HumanTodo** — an action only the operator can perform outside Hive, such as
  refreshing a CLI login or fixing DNS. Clearer than `HumanTask`, which collides
  with runner tasks.
- **Needs you** — the UI attention queue: open questions, human todos, blocked
  issue work items, and landing failures.

Names to change from the current code:

| Current name | Target name | Why |
| --- | --- | --- |
| `Project.work_source` | remove | Projects should not be mutually exclusive source modes. |
| Current `Workstream` | `WorkItem` | It is a unit of desired work, not the ongoing stream above a source. |
| `HumanTask` | `HumanTodo` | It is an operator action, not an agent execution. |

Optional later renames if they still feel clearer after the workstream refactor:

| Current name | Possible name | Why |
| --- | --- | --- |
| `GuessPropensity` | `ClarificationPolicy` | It controls when Hive guesses versus asks. |
| `OrchestratorRun` | `PlannerInvocation` | It records one planner/orchestrator call and its cost. |

## Proposed concepts

### Project

`Project` stops carrying `work_source`. It remains the top-level object:

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
smaller unit an orchestrator works on. In this design, that smaller unit should
be renamed to `WorkItem`. The hierarchy becomes:

```text
Project
  Workstream     # iteration goal, GitHub issues for repo X
    WorkItem     # auth-flow chunk, issue #42
    Task       # one runner execution attempt
```

Current implementation waypoint: the new top-level persisted object is named
`ProjectWorkstream` internally so the existing `workstreams` collection can keep
serving legacy work-item rows safely. The API already exposes the target shape:
`workstreams` are the top-level streams, while old per-unit rows are returned as
`work_items`.

### WorkItem

Rename the current smaller `Workstream` model to `WorkItem` and make workstream
ownership explicit:

```python
workstream_id: str = ""
repo: str = ""
source: WorkItemSource = manual | github_issue
external_ref: dict = {}  # issue number, URL, labels, attachment names
```

The existing issue-specific fields can be migrated gradually or kept as cached
columns for now. The important invariant is that every GitHub issue work item
belongs to a GitHub issues workstream and a concrete repo.

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

`Task` should eventually carry `workstream_id`, `work_item_id`, and `run_id`.
During migration, the existing `Task.workstream_id` keeps its current
work-item meaning until it can be renamed. Issue `resolve`, `review`, and
`preflight` tasks set all three fields. Iteration `work` and `verify` tasks set
the iteration workstream and work item, and leave `run_id` empty.

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

`scan-issues` should stop requiring `project.work_source == issues`. It should
be replaced by workstream/run endpoints, with old routes kept briefly as
compatibility wrappers.

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
issue-specific terminal states like `idle_no_open_issues` on the project.

During migration, the current enum can stay, but `blocked_clarity` and
`idle_no_open_issues` should become issue workstream statuses/counts rather
than project identities.

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
     run control-plane checks and optionally queue runner self-check

POST /api/projects/{project_id}/workstreams/{workstream_id}/sync
     fetch and reconcile GitHub issues without starting work

POST /api/projects/{project_id}/workstreams/{workstream_id}/issue-runs
     body: { scope, issue_numbers?, backend?, model? }
     sync, snapshot scope, and start deterministic issue solving

POST /api/issue-runs/{run_id}/cancel
     cancel queued issue work for that run
```

Backwards compatibility:

- `PATCH project.work_source` becomes a no-op or maps `issues` to "ensure an
  issue workstream exists for `spec_repo`".
- `POST /scan-issues` maps to "create or find issue workstream for `spec_repo`,
  run all_open_now".
- `POST /issues-preflight` maps to the issue workstream preflight.

## UI design

Keep the UI lean. Remove the setup-time `work source` toggle; project creation
always creates a project. Issue solving appears as an action inside the project,
not as a separate project type or a new app section.

### Project list

One row per project:

```text
Hive                     working        Work: 3 items          Issues: 1 running, 2 need you
Design Buddy             goal complete  Iteration done         Issues idle
```

The badge reports the strongest project-level condition. Secondary text shows
workstream summaries so issue work no longer changes the kind of project.

### Project page structure

Use the current project page shape with one added issues surface:

```text
Project header
  name, repo, state badge, pause switch

Primary switch
  Work | Issues

Right rail
  Needs you

Below / side
  Activity
  Settings disclosure
```

`Needs you` is the existing attention surface, not a tab. It contains
questions, human todos, issue blockers, and landing failures from every
workstream. It should stay visible while looking at either Work or Issues.

### Work view

This is the current spec/build page:

- intake panel if intake is incomplete
- goal-complete banner when applicable
- work item board
- policy strip: mode, autonomy, guess propensity, prod deploys
- compact activity slice for latest iteration work

No issue-specific controls appear here.

### Issues view

Top toolbar:

```text
Issues
[repo selector] [Preflight] [Sync] [Run issues]
```

`Run issues` opens a small run drawer:

```text
Run GitHub issues
Repo: ikamensh/hive
Scope:
  (*) selected issues
  ( ) all currently open
  ( ) scan only
Backend: codex default
[Start run]
```

Below the toolbar is a table, not a card grid:

```text
[ ] #42  Login probe fails on cold runner       Ready       labels...
[x] #43  Add issue run boundary                 Needs you   blocked at clarify
[ ] #44  Trace viewer raw link overflow         Reviewing   hive/issue-44
```

Columns:

- checkbox
- issue number and title
- state
- repo
- last agent note
- branch
- opened/updated age

Group filters:

- Ready
- Running
- Needs you
- Done
- Cancelled

Clicking an issue opens an inline detail drawer with:

- rendered issue body/comments
- attachments
- latest resolve/review task
- GitHub link
- branch link
- retry controls when blocked/rejected

### Needs you rail

Compact unified attention queue:

```text
Needs you
- Clarification request from iteration work item "auth flow"
- Issue #43 blocked: feature behavior decision needed
- Land issue #18 failed: merge conflict, branch intact
- Fix codex login on MacBook
```

Issue blockers should appear as attention items even if their authoritative text
is on GitHub. The item links to the issue, branch, and relevant task trace.

### Activity feed

Unified feed with workstream chips:

```text
[issues] review accepted #44  codex  $0.18  12m
[iteration] verify rejected auth flow cursor $0.07  8m
[issues] preflight passed ikamensh/hive
```

This makes it clear the same project budget and resource pool are being used.

### Settings disclosure

Project policies plus automation workstreams:

```text
Project policies
  mode, autonomy, guess propensity, prod deploys, daily budget

Repositories
  spec repo
  member repos

Automation workstreams
  Iteration goal                 enabled
  GitHub issues: ikamensh/hive   enabled   manual runs
  GitHub issues: org/api         disabled
```

Continuous issue polling is not part of this change. The settings can reserve
the vocabulary, but the default should be manual runs.

## Migration

1. Create an iteration workstream for every project.
2. Rename the current smaller `Workstream` records to `WorkItem` records.
3. For each `project.work_source == issues`, create a GitHub issues workstream
   for `project.spec_repo`.
4. Attach existing issue work items and resolve/review/preflight tasks to that
   workstream. If no run record exists, create a synthetic historical run.
5. Keep current issue statuses, but compute project state from all workstreams.
6. Remove the UI work-source picker.
7. After compatibility routes have no callers, delete `WorkSource`,
   issue-specific project states, and the dormant issue orchestrator tools.

## Test plan

- Unit: workstream creation, migration from old issue projects, preflight
  without `work_source`, issue run scoping, and one-active-issue-per-workstream
  invariant.
- API: create project, add issue workstream, sync, run selected issues, cancel
  run, retry blocked issue after a GitHub comment.
- Supervisor: issue tasks and iteration tasks share repo serialization and
  budget/resource blocking without suppressing unrelated ready work.
- Orchestrator: `mark_goal_complete` ignores issue workstream terminal work items
  but refuses completion while iteration work is unfinished.
- UI: mock project with both workstreams; verify project list, issues table, run
  drawer, `Needs you` rail, and activity feed at desktop and mobile widths.

## Recommended implementation order

1. Add target `Workstream`, `WorkItem`, `IssueRun`, `workstream_id`,
   `work_item_id`, and `run_id` while keeping existing fields and routes.
2. Move issue preflight/reconcile/advance to workstream-aware functions.
3. Replace setup `work_source` with issue workstreams in the API and UI.
4. Rework project state computation to aggregate workstreams.
5. Add run scoping and selected-issue UI.
6. Delete compatibility code and stale issue-mode docs once existing data is
   migrated.
