# Agent workflow and shared artifacts

Hive's canonical workflow is to prepare a plan with an administering agent,
submit it to an explicitly selected project, authorize execution, and retrieve
the result by plan ID. Hive's own planner produces the same document structure.
An imported plan needs neither an intake conversation nor an internal planner.

The web project page follows the same handoff: upload/paste → review and start →
live work items → Results & evidence. File submission uses the same endpoint as
the CLI; Export plan and Download result JSON return the shared artifacts.
Bookmark its `?plan=<id>` link to keep observing that execution after a newer
plan starts. See [the project workspace](project-launchpad.md).

The chief owns accepted revisions and live execution state. The author owns the
editable source file. A local chief and a managed chief expose the same API and
artifact contract; their storage and available machines differ. Editing a file
after submission does not change work already accepted by the chief.

## Prepare the handoff

Start in the repository where the project is being discussed. Establish the
iteration goal, expected outcomes, constraints, task order, and what code the
plan assumes. Choose the Hive project explicitly:

```bash
hive whoami
hive projects
hive project PROJECT
```

These examples assume the CLI is on PATH. Use `uv run hive` from the Hive source
checkout, or `hive --local ...` to select the local install. A remembered default
project in a repository is not implemented. Project selection remains the
administering agent's responsibility.

The canonical plan is plain TOML. Stable item keys support amendments; item
order is execution order. This is a new-plan example:

```toml
version = 1
plan_id = ""
revision = 0
goal = "Users can export deterministic CSV data from the CLI."

[context_documents]
"decisions.md" = "Use Python's standard library. Preserve the existing CLI.\n"

[[items]]
key = "csv-export"
repo = "https://github.com/you/core.git"
title = "Add deterministic CSV export"
story = "A user can export the selected records to a CSV file."
constraints = "Keep record ordering stable; add no dependencies."
notes = """
Cover empty input and records containing commas and newlines.
Run the public CLI in the tests and verify the resulting CSV.
"""
```

For a project spanning repositories, give each item its target `repo`. An empty
item repo resolves to the project's spec home when submitted, and that resolved
repo is retained. A plan runs in strict order across its repositories; it does
not express a dependency graph or parallel branches. Choose a project validation
command that works in each target repository.

`context_documents` is a map of names to inline text, preserved in the accepted
revision and supplied to execution attempts. A name is a label, not a local path
that Hive reads or uploads. Include the needed document text explicitly.
`repo_baselines` optionally maps repository URLs to the commits used while
planning. These baselines are advisory context: Hive does not verify them,
check out those SHAs, capture dirty files, or transfer unpushed code. The
administering agent must make the required code available to the runners;
fresh item branches start from the remote default branch after prior merges.

Markdown remains an initial-import shortcut: a `# Goal` followed by ordered
`## Task title` sections puts each task body into its notes. Export TOML before
editing a submitted plan. The canonical representation preserves prose,
headings, code fences, structured fields, and stable identities without relying
on Markdown headings as field delimiters. The authoritative schema is
[`PlanDocument`](../hive/_workstreams/plan_document.py).

## Check, submit, and start

```bash
hive plan-check plan.toml
hive plan-import PROJECT plan.toml --validate 'make check'
hive plan PROJECT
hive plan-ready PLAN_ID
hive plan-approve PROJECT
```

`plan-check` works offline and validates the document format. It does not check
repository access, starting commits, agent availability, or whether the proposed
work is feasible. A submission must contain at least one item. Add
`--repo https://github.com/you/core.git` when creating a project through import.
Existing project settings are preserved unless options such as `--validate` or
`--prefer` explicitly change them. Agent selection, session allowances, and
budgets remain project policy configured with `hive set`.

`plan-ready PLAN_ID` reads the chief's current execution prerequisites without
starting a task or contacting GitHub. It uses dispatch's concrete model selection,
included-only policy, session allowances, runner capabilities, quota windows and
retry checkout affinity. Busy runners and conflicting repository work appear as
waiting. The web shows these checks beside approval, with direct repair links.
Use `--json` for `plan_id`, `revision`, `observed_at`, `ready` and individual checks.
`ready` means no observed blocker in this snapshot. It does not reserve a runner,
fund the entire build/review cycle, prove repository access, or guarantee success.
An automatic reviewer follows the eventual builder choice; current review tasks
retain their selected agent and validation settings. Changes to validation apply
to newly queued reviews. Approval remains available while work waits for capacity.

When the operator has authorized execution of this revision, use
`hive plan-import PROJECT plan.toml --start` to submit and approve together.
Both authorship paths require approval before execution. Hive-authored drafts
are available through `hive plan-propose PROJECT` and the same export/review
commands; configuring its planner is covered in [the README](../README.md).

Save the returned `plan.id`, `accepted_revision`, and `plan.input_digest`.
The ID identifies the execution to follow. The digest hashes the canonical
accepted TOML for that revision, including assigned identities and resolved
repositories. It is not a hash of the original Markdown or TOML source text.

```bash
hive plan-export PLAN_ID -o accepted.toml
hive plan-export PLAN_ID --revision 1 -o revision-1.toml
hive plan-export PLAN_ID --revision 1 --submitted -o original-input.txt
```

Canonical export returns an editable document with the accepted `plan_id` and
`revision`. `--submitted` returns the original submitted text, which can differ
from the canonical export; UTF-8 source line endings are preserved. Submitted
revisions are retained by the chief.
Repeating the same source for the same project reuses its accepted submission,
including after completion or abandonment; it does not create another run.

## Monitor and inspect

```bash
hive plan-wait PLAN_ID --timeout 60 --json
hive plan-result PLAN_ID -o result.json
```

Wait follows the specified plan even if the project later starts another one.
It returns one observation with `reason`, `plan_id`, elapsed time, and a state
snapshot. Waiting never approves, pauses, retries, or cancels work.

| Wait reason | Administering agent's next action |
| --- | --- |
| `completed` | Read `plan-result`, inspect item outcomes and verification evidence. |
| `abandoned` | Read the result; `terminal` tells whether running attempts have drained. |
| `needs_attention` | Read the snapshot and `hive inbox`; approve a reviewed draft, answer a question, or repair the parked item as appropriate. |
| `waiting_capacity` | Inspect the state reason and `hive show agents`; resolve access, allowance, or availability, then wait again. |
| `timed_out` | Read the observation and wait again if continued monitoring is intended. A timeout does not stop Hive. |

The JSON execution result includes input revision/digest, separate done and
cancelled counts, each item's outcome, attempt revisions and reports, review and
validation evidence, landing commits, trace/artifact references, and relevant
open questions/todos. `hive task TASK_ID` and `hive trace TASK_ID` provide deeper
inspection. The result's `captured_at` changes on export: it is a dated snapshot,
not a frozen JSON file written once.

`terminal` means execution has ended, including draining any cancelled attempts.
An execution can be `done`, `partial`, or `abandoned`; active snapshots also
distinguish pending, running, blocked, and stopping. A complete plan can contain
cancelled items. Inspect that distinction before assessing whether the goal was
achieved. Results and completion reporting work with the planner disabled;
the higher project goal verdict remains separate.

## Amend and recover

```bash
hive plan-export PLAN_ID -o amendment.toml
# Edit future work; preserve plan_id, revision, item keys, and existing items.
hive plan-check amendment.toml
hive plan-import PROJECT amendment.toml --start
```

An amendment names the revision it was based on; the chief assigns the next
revision when accepting changes. Changed queued instructions become proposals
and need renewed approval. Leave off `--start` to submit them for review.
Execution attempts retain their original instructions and revision. Started or
terminal items cannot be rewritten or reordered through an amendment. Shared
goal/context cannot change once an item has started; put new requirements in
eligible future items instead.

Omitting an existing key does not cancel its item: the submission is rejected.
Use `hive plan-item-cancel ITEM_ID`, retaining the item in future exports. Stop
an active task with `hive cancel TASK_ID` and wait for its report before editing
and retrying a parked item with `hive plan-retry ITEM_ID`. To stop the whole plan,
use `hive plan-abandon PROJECT`. `--append` accepts a document of additional
items with the same goal; they still need approval, optionally through `--start`.

| Failure | Recovery |
| --- | --- |
| Unknown field, malformed TOML, duplicate key | Correct the file and rerun `plan-check`; these are document errors. |
| HTTP 409, stale revision or another live plan | Export the current plan and reconcile your changes. Do not manually bump the revision to bypass the conflict. |
| Lost response or chief interrupted during submission | Retry the same submission. The retained prepared submission is replayed before the accepted revision advances, preserving the plan identity. |
| Edit touches an active/completed item, or omits a key | Keep those instructions and keys; amend eligible work or perform an explicit cancellation. |
| Review or validation failure | Inspect the report. Hive makes bounded repairs; if it parks, fix the cause or amend the item and retry. |

Approval publishes `iteration-plan.toml` to the spec home as a best-effort
snapshot; a publication failure creates a todo while execution continues.
Subsequent revisions are retained by the chief and retrieved through export.
They are not automatically synchronized into Git. Draft files may be tracked
in a repository, but pushing a file does not launch work and runtime heartbeats
do not create Git commits. There is no bidirectional file watcher.

## Executable evidence

Run the isolated artifact demonstration from the Hive checkout:

```bash
uv run python scripts/demo_plan_artifacts.py --output-dir /tmp/hive-artifact-demo-new
# With an authenticated coding agent:
uv run python scripts/demo_plan_artifacts.py --output-dir /tmp/hive-artifact-codex-new --backend codex
```

Use a fresh output directory. The default uses a scripted agent;
`--backend opencode` is also available. The demo uses a local FileStore chief and a local
Git origin with isolated landing transport, preserving exported inputs, results,
and checks in its output directory. It does not exercise the deployed chief or
GitHub's production merge endpoint.

On 2026-09-06, the real Codex variant at
`~/.local/share/hive-demos/plan-artifacts-20260906` passed: two initially failing
tests passed after independent build/review sessions. Plan `3eb0d22bdf1b` checked
and landed SHA `0aef191c5111f585bb8b96b9a916a5c4e62d609e`.
Two chief restarts preserved state, and
a duplicate submission retained one plan. Managed storage uses the same API
contract; this recorded live exercise verified local FileStore execution.

See [acceptance examples](../acceptance/plan-artifacts.md) and the integration
tests referenced in the [code map](code-map.md) for the maintained contract.
