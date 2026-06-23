# Project launchpad

The project page is a **launchpad**: a place to *start work*, not a card of
settings. It surfaces the jobs available on a project and shows live state
(running jobs, checkouts, attention queue, activity) around them. See
`CONTEXT.md` for the glossary (Launchpad, Job, Directive, Checkout, Drift, Sync).

This supersedes the toggle/settings-first project view and folds the
`work | issues | tests` tabs into launchable job types whose deep tables become
drill-downs.

## Page structure (configured project)

```text
Header            name · repo · state badge · pause · Actions

Start a job       ┌─ Give Hive a task ───────────────┐  [Fix issues]
(primary)         │  free-form directive box (hero)  │  [Run tests]
                  └──────────────────────────────────┘  [Advance build]
                                                         [Sync a machine]

In flight         running jobs: issue runs, test episodes, directives, syncs

Machines &        per machine × this project's repos:
checkouts           checkout? · HEAD · ahead/behind/dirty · env readiness
                    drift → [Sync] (stubbed)

Needs you         questions · human todos · blockers (unchanged surface)

Activity          unified feed with workstream/job chips

Settings          disclosure: policies · repos · workstream toggles
(disclosure)
```

Unconfigured / `intake` projects keep the existing intake flow as their "first
job"; the launchpad shape applies once the project is configured.

The full issues table ([issues.tsx](../web/src/features/project/issues.tsx)) and
stories table ([testing.tsx](../web/src/features/project/testing.tsx)) open
behind their launcher (panel/route), not as always-present tabs.

## Jobs

A **Job** is the operator's word for what you launch here. Concrete forms:

| Job | Backs onto | Semantics |
| --- | --- | --- |
| Advance build | orchestrator / iteration workstream | ongoing; gated by pause |
| Fix issues | `IssueRun` on a `github_issues` workstream | one-shot run (mode opt-in later) |
| Run tests | `TestEpisode` on a `testing` workstream | one-shot run |
| Sync a machine | agent sync run on a `Checkout` | one-shot; see Sync below |
| Give Hive a task | `Directive` | persisted ask; Hive routes it |

Default is a bounded one-shot run you launch and watch. A continuous "mode" that
keeps a job type going until you stop it is an opt-in per-job toggle, layered on
later — not the default.

## Directive (general task)

A `Directive` is a persisted, human-authored ask to a project — the hero box on
the launchpad. It is distinct from the iteration goal (standing strategy, one at
a time) and from GitHub issues (external source).

This pass: **real model + create/list API + real launchpad UI; stubbed brain.**

- Submitting a directive persists it at `triaging`.
- A routing panel shows a *suggested* executor (backend/model) + machine, derived
  from a simple heuristic over live capacity, clearly labeled "preview — not
  dispatched."
- Nothing is dispatched. The triage→assign→seed-work-items→track-to-done engine
  is intentionally unbuilt.

```python
class DirectiveStatus(StrEnum):
    triaging = "triaging"        # received, no executor chosen yet
    awaiting_executor = "awaiting_executor"  # routed (preview), not dispatched
    working = "working"          # (future) seeded work in flight
    done = "done"
    cancelled = "cancelled"

class Directive(BaseModel):
    id; workspace_id; project_id
    text: str                    # the human ask
    status: DirectiveStatus = triaging
    suggested_backend: str = ""  # preview routing
    suggested_model: str = ""
    suggested_machine_id: str = ""
    routing_note: str = ""       # one-line rationale (preview)
    created_at: float
```

## Checkout & drift

A `Checkout` is a project repo's working copy on one machine. One per
`(machine, repo)`. The remote is authoritative; the checkout is where work can
accumulate and possibly drift.

This pass: **real read path; stubbed sync; protective reset deferred.**

```python
class Checkout(BaseModel):
    id; workspace_id; machine_id; repo
    exists: bool = False
    head_sha: str = ""
    branch: str = ""
    ahead: int = 0              # local commits not on origin
    behind: int = 0
    dirty: bool = False         # uncommitted working-tree changes
    env_status: str = "unknown" # reserved: dependency-setup readiness
    last_reported_at: float
```

- **Read path (this pass):** the runner reports per-repo git facts for the repos
  it has checked out, piggybacked on its existing 30s heartbeat
  ([daemon.py register](../hive/runner/daemon.py)). The chief upserts a
  `Checkout` per `(machine, repo)`. The launchpad shows drift per machine.
- **Drift** = `ahead > 0` or `dirty`. It means real work may live only on one
  machine.
- **Sync (deferred behavior, stubbed button this pass):** an *agent* job, not a
  `git push`. The sync agent judges whether the dirty tree is worth committing
  and whether commits belong on `main` (so other agents build on top), and when
  unsure raises a `Question` instead of pushing.
- **Drift safety (fast follow):** before any destructive `reset --hard`/`clean`,
  the runner pushes drift to a throwaway `hive/backup/<ts>` branch on origin —
  reusing the existing `fresh_branch` backup pattern
  ([daemon.py:367](../hive/runner/daemon.py)) — so a task racing in can never
  silently destroy machine-local work. The agent sync job does the thoughtful
  consolidation.

## Build order

1. `Directive` + `Checkout` models; store collections.
2. API: directive create/list (+ preview routing heuristic); checkout report
   from runner heartbeat; both folded into `GET /api/projects/{id}`.
3. Runner: report checkout git facts in the heartbeat payload.
4. Web: launchpad IA rework — directive box, job tiles, machines panel, drill-down
   the issues/stories tables.
5. Fast follow (separate change): protective backup-before-reset + agent sync job.

## Test plan

- Model round-trip (save/load invariance) for `Directive` and `Checkout`.
- API: create directive → appears in project payload at `triaging` with a preview
  suggestion; runner checkout report → `Checkout` upserted; drift computed from
  ahead/dirty.
- Property: `drift == (ahead > 0 or dirty)`; a checkout that never reported stays
  `exists=False`.
- UI: mock project renders launchpad, directive box, job tiles, machines panel
  with mixed drift states, at desktop and mobile widths (screenshot).
