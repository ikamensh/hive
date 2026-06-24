# Operational runbooks

Repeatable procedures for the few runtime events that recur. Each entry: how the
event is detected, where the evidence lives, and how to act. Add an entry the
first time an incident takes more than a glance to diagnose.

## Runner went offline mid-task

**Symptom.** A task ends `failed` with `result_text = "Runner <id> went offline
mid-task."`, and the chief log carries a matching `WARNING`:

```
runner <name> (<id>) silent for <N>s past 300s limit — failing orphaned <kind> task <id> on <repo>
```

### How the detection works (so the numbers mean something)

A runner refreshes its `last_seen` by re-registering every 30s — a daemon thread
in `hive/runner/_daemon.py` (`heartbeat()`) so a long-running task can't starve
it. The chief reads `last_seen` through two thresholds:

| Threshold | Constant | Meaning |
|---|---|---|
| 90s | `Runner.ONLINE_WINDOW_S` (`hive/models.py`) | `Runner.online()` — drives dispatch eligibility and the UI's online/offline badge |
| 300s | `RUNNER_OFFLINE_TASK_FAIL_S` (`hive/_control/supervisor.py`) | `Supervisor.fail_orphaned_tasks()` declares the runner gone and fails its in-flight task |

So "offline mid-task" means: **no heartbeat for 5 minutes while a task was
`running`.** Three missed heartbeats put the badge red; ten failed the task. The
`fail_orphaned_tasks()` pass runs every supervisor tick, logs the WARNING above,
and wakes the orchestrator so the project replans.

### Where the logs are

1. **Chief** — the WARNING is the entry point. On the VM:
   `deploy/vm.sh logs chief 200` (journalctl). Grep for the runner id or `silent`.
   This tells you *when* the chief gave up and *which* task died, but never *why*
   the runner went quiet — for that you go to the runner's own machine.

2. **Runner** — the cause lives here. First map the runner id to a machine:
   `Runner.id → Runner.machine_id → Machine` (name, hostname, kind). Then read
   that machine's log **around `Runner.last_seen`** (the WARNING's silence window
   ends there):
   - **Mac (launchd):** `~/Library/Logs/hive/runner.log`
   - **VM (systemd):** `deploy/vm.sh logs runner 200`, or
     `journalctl -u hive-runner --since "<last_seen-5min>"`

### Reading the runner log around `last_seen`

- **Log goes abruptly silent, then resumes minutes later with a fresh
  `registered as …`** → the host slept or lost the network. On a Mac this is the
  common one: lid closed / Wi-Fi dropped. launchd `KeepAlive` + reconnect-after-
  sleep brings it back and it re-registers under the same name (stable name →
  same machine row → same runner row), but the in-flight task was already failed.
- **`transient error: … — retrying in 10s` repeating for >5 min** → a network
  partition to the chief, not a runner crash. The runner is healthy; the path to
  the chief isn't.
- **A traceback / the process exits, then a launchd/systemd restart line** →
  the runner crashed (OOM, an unhandled exception in `execute`). The restart
  re-registers with `boot=True`, which requeues whatever non-probe task was in
  flight as `pending` (see `api.py` register handler) — distinct from the orphan-
  fail path, and the usual reason a failed task quietly re-runs and succeeds.
- **Nothing at all, host never came back** → the machine is down/decommissioned.
  Check `Machine.last_seen`; if it's a laptop that's just closed, no action.

### Acting on it

- **Transient (sleep / brief network blip):** none. The orchestrator already
  replanned; the task re-dispatches to any capable runner. This is expected for
  laptop runners and not an incident.
- **Repeated on one machine:** that runner is flaky — check its host (power
  settings preventing sleep, Wi-Fi, the VM's health via `deploy/vm.sh status`).
- **A crash (traceback in the runner log):** that's a real bug — capture the
  traceback and fix it; the offline was a symptom.

### Note on long tasks

A task that legitimately runs longer than 5 minutes does **not** trip this — the
heartbeat thread keeps `last_seen` fresh independently of the work loop. If a
long, healthy task is being failed as "offline," the heartbeat thread itself died
(crashed or blocked); look for its absence in the runner log, not for the task.
