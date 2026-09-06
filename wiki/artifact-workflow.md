# Shared artifacts for agent-administered Hive

Status: design discussion, 2026-09-06. The principles below capture the user's
direction; the proposed lifecycle is a recommendation, not implemented behavior
or an approved implementation plan.

## Direction established in discussion

- Hive is administered through its CLI by an agent working with the operator.
  Together they can develop the mission, iteration goal, repository understanding,
  and task breakdown before handing execution to Hive.
- Hive may also plan autonomously. Both authors should produce the same kind of
  planning artifacts, usable as files and accepted by the same execution path.
- A project spans multiple repositories. Selecting the right Hive project can
  remain the administering agent's responsibility. A remembered default in a
  main repository is optional convenience, not the definition of project scope.
- Explore clear file inputs, outputs, and durable artifact history for other
  operations too. How these relate to distributed runtime state remains a design
  choice; separate file-based and distributed workflows are not yet a decision.

## Recommendation: shared documents, one execution authority

Planning authorship and runtime placement are independent choices. An external
agent or Hive can author a plan; a local or remote chief can execute it. Existing
local FileStore/LocalBlobStore and managed Firestore/GCS provide the storage
alternatives without requiring different artifact semantics.

Distinguish three lifecycles:

| Material | Authority | File behavior |
| --- | --- | --- |
| Draft instructions | The author editing that draft | Editable, portable, optionally tracked in Git |
| Submitted instructions | The exact revision accepted by the chief | Retained and exportable without changing its meaning |
| Execution state and results | The chief coordinating the work | Status exports are dated observations; final results are durable artifacts |

The chief keeps the submitted input and its execution facts. It does not depend
on the originating laptop or its checkout remaining online. Editing a source
file after submission does not silently change executing work. Exporting status
does not rewrite the author's draft.

Git remains useful for project knowledge and reviewed plan history. Starting a
job should explicitly submit its documents to the selected chief; pushing a file
need not implicitly launch work. Runtime heartbeats and runner leases remain
coordinated execution state, with no requirement to commit them into a repository.

## Proposed planning lifecycle

1. Prepare a brief and plan files, locally or through Hive's planner. Both paths
   use one versioned plan schema and a lossless file representation. The web
   editor must use the same document semantics too.
2. Check and submit a specific revision to an explicitly selected project.
   Return the accepted project, plan ID, revision, input digest, resolved
   repository references, and execution settings.
3. Start that revision under the operator's authorization. Hive-authored plans
   obey the same authorization rules; authorship alone does not authorize work.
   An authorized autonomous workflow can chain these stages without a manual
   file export and re-import.
4. Inspect or export current status, questions, task evidence, and outputs. An
   administering agent can reconnect from another machine using the returned ID.
5. Amend against the current accepted revision. Stable item identities and an
   expected prior revision prevent stale drafts from overwriting newer work.
   A retry of the same submission returns the same acceptance receipt.
6. When execution ends, produce a deterministic result containing landed,
   cancelled, and unresolved items, tested and merged commits, validation evidence,
   and output references. A narrative assessment can explain whether the goal
   was met, but is not required to record execution completion.

Submitted revisions remain available. An active attempt retains the instructions
it started with. Amendments affect eligible future work; changing an active
attempt's instructions requires an explicit interruption. Removing a section
from a draft must not implicitly cancel running work.

## Multiple repositories and references

Use the existing spec home as the natural location for shared artifacts; it can
be the main implementation repository or a dedicated documentation repository.
The selected Hive project supplies its member repository registry. A submission
records the resolved repository mapping, so later registry edits do not silently
retarget accepted work. Items name their target when a project default is
insufficient. No checkout discovery is required to launch explicitly.

Project selection and code availability are separate concerns. Instructions
written against unpushed code still need an explicit way to make that code
available to remote runners. Start with published code and recorded repository
baselines; avoid automatically packaging an entire working tree.

Submission captures explicitly included local document bytes and resolves their
relative paths from a defined artifact root. Referenced repository documents
record the revision used. A planning baseline describes the code the author
understood; it does not force every later item to restart from that commit and
discard earlier items' merges.

## Generalize the convention through concrete operations

| Operation | Durable input | Durable output |
| --- | --- | --- |
| Planning | Brief, goals, constraints, repository context | Plan |
| Execution | Accepted plan revision | Execution result and evidence |
| Testing | Acceptance stories and testability contract | Test report and findings |
| Critique | Selected specification or plan revision | Findings and proposed changes |

Each operation should accept and emit its documented representation through the
CLI. Documents can be read or written directly by an external agent; Hive can
produce them through the same module interface. Do not require every internal
module to serialize a temporary file simply to call the next module.

Pause, resume, cancellation, and retry remain explicit actions with receipts.
File representation should make durable work easier to prepare, inspect, and
compose; it should not require editing state files for a simple action.

Begin with the plan document and execution result. Establish the convention
through these real uses before introducing a generic operation framework.

## Current implementation gaps

- Markdown import and planner-created plans already converge on `Plan` and
  `PlanItem`, but there is no common lossless document round trip. The import
  parser puts task bodies in notes; the planner supplies structured item fields.
- `iteration-plan.md` is written once at activation. Its commit identity is not
  retained, and subsequent edits/amendments do not update that snapshot.
- Submission identities, immutable revisions, stale-edit checks, and retained
  repository mappings are absent.
- CLI watching emits snapshots until the project's latest plan terminates; it
  lacks a bounded wait for a specified plan to need attention or complete.
- Final project completion currently relies on the planner, even though imported
  plans can execute with planning disabled. A deterministic execution result is
  needed for both authorship paths.

## Choices still open

- Exact document syntax and directory convention. Prefer one canonical plan
  representation with readable prose and enough structure for stable identities;
  do not maintain independently editable Markdown and JSON versions.
- Whether publishing draft/final artifacts to the spec home's Git history is
  automatic or explicit. Either way, execution must retain accepted input and
  output independently of whether that publication succeeds.
- The policy for repository drift since planning: record a baseline, require
  an expected revision where needed, and define when revalidation is necessary.
- The authorization policy for fully autonomous planning and amendments. The
  artifact format should not encode a mandatory human interview.

## Behavioral examples to validate before implementation is complete

- External and Hive-authored versions of the same plan preserve identical
  meaning through export, edit, validation, submission, and execution.
- Submit from a laptop to a remote chief, disconnect the laptop, then retrieve
  the exact accepted input and result from another client.
- Submit twice after a lost response: only one plan is accepted and executed.
- Two agents edit revision 7: after one submits revision 8, the other's update
  is rejected with the current revision and an inspectable difference.
- An amendment changes queued work while the active task retains its original
  instructions and previously completed work retains its evidence.
- A two-repository plan records both repository identities and preserves earlier
  items' merges when starting later items.
- With the planner disabled, both local and managed runtime modes produce a
  terminal execution result, distinguishing landed work from cancellations.
