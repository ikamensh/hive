# story: runner-capacity-probe [ui]
As an operator I can register and probe runner capacity so that Hive dispatches tasks only to resources that are actually usable.

## Rules
- Before registration, Hive can report detected supported agent backends on the local machine or a clear "none found" result.
- Starting a runner registers an online runner with one resource row for each advertised backend and any discovered capabilities, visible in both the Resources page and CLI.
- Runner protocol endpoints reject missing or incorrect runner tokens.
- Unknown advertised backends are auto-probed on registration or first need; a manual probe remains available after the operator fixes a login or environment problem.
- A backend probe records time, result evidence, and usable/unusable status for the specific `(runner, backend)` capacity.
- Browser and Docker capabilities are probed before UI or containerized test work is dispatched to that resource.
- Auth failures, quota exhaustion, missing CLIs, or crashes mark the affected capacity unavailable or cooling down and create a concrete human todo when operator action is required.
- Pending work whose required backend or capability is unavailable remains `blocked: resources` and is not dispatched.
- Stopping and restarting a runner does not leave work stuck forever: boot registration refreshes the runner heartbeat, requeues any tasks that died with the old runner process, and leaves heartbeat-only registrations from a still-running process untouched.

## Examples
- Given a local runner is started against the chief with a valid token
  When I open the Resources page
  Then I see the runner online with resource rows for its advertised backends and the latest probe state for each unknown or checked resource
- Given a backend is advertised but its CLI login has expired
  When the automatic or manual backend probe runs
  Then the resource is marked unusable or cooling down with evidence and I see a human todo telling me what to refresh
- Given a UI testing sweep needs a browser-capable runner
  When no advertised resource has a usable browser probe
  Then the sweep is not dispatched and the attention queue names the missing capability and machine to fix
- Given a pending task requires a backend that no online usable runner offers
  When the supervisor computes project state
  Then the project shows `blocked: resources` and the task is not assigned to a runner
- Given a runner is stopped while a task is assigned or executing
  When the runner is restarted and re-registers
  Then the boot registration refreshes its heartbeat, the died task is requeued, and other running processes are untouched
