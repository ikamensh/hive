# Hive Domain

Hive coordinates AI project work across user-owned machines and the AI agents the user has access to. This glossary keeps operator-facing concepts distinct from execution plumbing.

## Machines

**Machine**:
A durable computer the user recognizes as available to Hive. Machines are the user-facing unit of capacity and may be either cloud servers or personal computers.
_Avoid_: Runner, host, resource

**Cloud Server**:
A machine expected to stay online unless the user explicitly stops it. It is suitable for always-on capacity and background project work.
_Avoid_: Runner, VM

**Personal Computer**:
A machine the user also uses for ordinary work and may close, move, sleep, or disconnect. Its capacity is useful but should not be assumed always-on.
_Avoid_: Laptop

**Runner**:
A technical access link that lets Hive use one machine for work. A runner belongs to a machine and should not be presented as a machine type; in normal operation, there is one runner per machine.
_Avoid_: Server, machine, agent

## Agents & access

**Subscription**:
A durable, account-level access to an AI provider — a paid plan or an API key (Claude Max, ChatGPT Pro, Cursor, a Gemini key). It is the user's longest-lived unit of capacity, changes rarely, and is what an [[Agent]] must be authenticated against before it can run.
_Avoid_: Plan, account, provider, entitlement

**Agent**:
A model-backed coding tool authenticated on a machine and ready for Hive to assign work to, realized from a [[Subscription]]. Shorter-lived than the subscription behind it: live availability also needs the machine online, the login still valid, and the provider not rate-limiting it. An agent is the individual unit; "resource" is only a category word for capacity in aggregate and must never name a single one.
_Avoid_: Runner, resource (for an individual), subscription

**Licensing Mode**:
How a [[Subscription]]'s credential may be placed across machines: *portable* (an API key Hive can copy to any machine, e.g. Cursor) or *machine-bound* (a login tied to where the human authenticated, e.g. Claude Max). It decides whether Hive can stand up an [[Agent]] itself or must ask the human to log in on a specific machine.
_Avoid_: License, tier

**Scout**:
An agent acting in the intake role — aligning a project's mission, next iteration, and assumptions before planning begins. "Scout" names what the agent is doing, not a separate kind of agent; the same machine-bound agent that does project work can serve as a scout.
_Avoid_: intake bot, planner

**Trusted scout**:
A backend+model combination Hive permits to run intake. Intake is high-leverage, so only a curated set qualifies, not every available agent. "Trusted" qualifies the backend (e.g. codex gpt-5.5, claude opus), never a specific machine's install — so trust is a single yes/no policy, not a per-machine status.
_Avoid_: verified agent, approved runner

## Checkouts & sync

**Checkout**:
A project repo's working copy on one specific [[Machine]] — the unit Hive tracks to answer "where does this project physically exist, and is any work there missing from the remote?". One per (machine, repo). Carries the git facts (HEAD commit, ahead/behind/dirty vs origin, last seen) and a reserved *environment readiness* attribute that stays unknown until a real dependency-setup step exists. A checkout is observed, not authoritative: the remote is the source of truth, the checkout is a place work can accumulate.
_Avoid_: clone, workdir, working copy (in UI), repo

**Drift**:
A checkout whose local state the remote does not have — un-pushed commits and/or an uncommitted working tree. Drift is the signal that real work may live only on one machine; it is detected cheaply from a checkout's git facts.
_Avoid_: dirty (alone), divergence, unsynced (as a noun)

**Sync**:
An agent [[Job]] that resolves a checkout's [[drift]], not a mechanical `git push`. A sync agent judges whether the dirty tree is worth committing and whether the commits belong on `main` so other agents build on top — and when unsure, raises a [[Question]] for the human instead of pushing. Its value is consolidating machine-local work onto the shared remote.
_Avoid_: push, pull, backup

## Launchpad

**Launchpad**:
What a project page is for: a place to *start work*, not a card of settings. It offers the [[Job]]s available on this project and shows live state (running jobs, [[Checkout]]s, attention queue, activity) around them.
_Avoid_: dashboard, project card

**Job**:
The operator's word for a unit of work you launch from a project's [[Launchpad]] — fixing issues, running tests, advancing the build toward the goal, syncing a machine, or a [[Directive]]. "Job" names the launch from the operator's side; it is not one persisted type — its concrete forms differ by kind (an issue run, a test episode, an orchestrator activation, a sync agent run, a directive). Reserve [[Task]]-the-model for one runner execution attempt.
_Avoid_: task (the persisted execution unit), run (only the bounded-batch forms are runs)

**Directive**:
A persisted, human-authored ask to a project — the "just tell Hive what you want" entry on the [[Launchpad]]. Hive triages it, assigns an executor (backend/model) and a [[Machine]], may seed work items, and tracks it to done. Distinct from the iteration goal (the project's standing strategic objective, one at a time) and from a GitHub issue (an external source): a directive is the user's own direct request. Reserve "task" for runner execution plumbing.
_Avoid_: task, goal, request, job (a directive is one kind of job, not the umbrella)
