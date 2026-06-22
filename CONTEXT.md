# Hive Domain

Hive coordinates AI project work across user-owned machines and agent backends. This glossary keeps operator-facing concepts distinct from execution plumbing.

## Language

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

**Agent**:
A model-backed coding tool available on a machine for Hive to assign work to. Agents are shown under machines because their availability depends on that machine's access, credentials, and local setup. An agent is the individual unit; "resource" is only a category word for capacity in aggregate and must never name a single one.
_Avoid_: Runner, resource (for an individual), subscription

**Scout**:
An agent acting in the intake role — aligning a project's mission, next iteration, and assumptions before planning begins. "Scout" names what the agent is doing, not a separate kind of agent; the same machine-bound agent that does project work can serve as a scout.
_Avoid_: intake bot, planner

**Trusted scout**:
A backend+model combination Hive permits to run intake. Intake is high-leverage, so only a curated set qualifies, not every available agent. "Trusted" qualifies the backend (e.g. codex gpt-5.5, claude opus), never a specific machine's install — so trust is a single yes/no policy, not a per-machine status.
_Avoid_: verified agent, approved runner
