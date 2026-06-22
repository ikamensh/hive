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
A model-backed coding tool authenticated on a machine and ready for Hive to assign work to, realized from a [[Subscription]]. Shorter-lived than the subscription behind it: live availability also needs the machine online, the login still valid, and the provider not rate-limiting it.
_Avoid_: Runner, resource, subscription

**Licensing Mode**:
How a [[Subscription]]'s credential may be placed across machines: *portable* (an API key Hive can copy to any machine, e.g. Cursor) or *machine-bound* (a login tied to where the human authenticated, e.g. Claude Max). It decides whether Hive can stand up an [[Agent]] itself or must ask the human to log in on a specific machine.
_Avoid_: License, tier
