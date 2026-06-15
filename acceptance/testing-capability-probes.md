# story: testing-capability-probes [api]
As a Hive operator I can trust browser and Docker capability badges so that testing work only dispatches to runners that have proven they can run the required environment.

## Rules
- A runner merely advertising `browser` or `docker` capability must not make those capabilities usable without a probe or equivalent proof.
- A story requiring `browser` or `docker` should remain blocked_resources until an online resource has a usable backend and the required capabilities are proven usable.
- The operator should see a concrete todo when testing work is blocked on missing capabilities.

## Examples
- Given a runner registers a usable backend and only advertises `browser` and `docker`
  When I inspect the resource state before any capability proof has run
  Then browser/docker are not treated as proven usable for dispatch.
- Given a pending UI testing task requires `browser`
  When no resource has proven browser capability
  Then Hive does not dispatch the task and files an operator todo explaining the missing capability.
