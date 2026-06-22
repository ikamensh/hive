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
A model-backed coding tool available on a machine for Hive to assign work to. Agents are shown under machines because their availability depends on that machine's access, credentials, and local setup.
_Avoid_: Runner, resource, subscription
