# story: runner-capacity-probe [ui]
As an operator I can register and probe runner capacity so that Hive dispatches tasks only to resources that are actually usable.

## Rules
- Before registration, Hive can report detected supported agent backends on the local machine or a clear "none found" result.
- Starting a runner registers an online runner with one resource row for each advertised backend, visible in both the Resources page and CLI.
- Runner protocol endpoints reject missing or incorrect runner tokens.
- A backend probe records time, result evidence, and usable/unusable status for the specific `(runner, backend)` capacity.
- Auth failures, quota exhaustion, missing CLIs, or crashes mark the affected capacity unavailable or cooling down and create a concrete human todo when operator action is required.
- Pending work whose required backend or capability is unavailable remains `blocked: resources` and is not dispatched.

## Examples
- Given a local runner is started against the chief with a valid token
  When I open the Resources page
  Then I see the runner online with resource rows for its advertised backends
- Given a backend is advertised but its CLI login has expired
  When I run the backend probe
  Then the resource is marked unusable or cooling down with evidence and I see a human todo telling me what to refresh
- Given a pending task requires a backend that no online usable runner offers
  When the supervisor computes project state
  Then the project shows `blocked: resources` and the task is not assigned to a runner
