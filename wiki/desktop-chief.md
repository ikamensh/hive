# Choosing this Mac's chief

`hive switch local` drains this Mac's remote runner, starts a local chief and
worker under launchd, and routes the CLI and menu bar to it. The menu bar's
**Switch to local chief** action does the same. A task already running finishes
before the other chief gets this Mac's capacity.

The local dashboard is http://127.0.0.1:8787 (loopback only, no site password).
Its projects and artifacts live in `~/.local/share/hive-local`; remote data is
not copied. This is separate from `hive run --local`, which remains the foreground
development command on port 8000.

`hive switch remote` drains the local worker, stops the local chief, and resumes
the enrolled remote runner. Remote URL and credentials remain in
`~/.config/hive/runner.env`. Other machines and the remote chief keep running.

`hive switch status` shows the selected chief and this Mac's runner state.
`hive whoami` verifies its connection. An explicit `HIVE_URL=...` or `--local`
still overrides the CLI target for that invocation (`--local` means the dev
chief on port 8000, not the desktop chief on 8787).

The selection persists in `~/.config/hive/selected-chief`. The local chief's
LaunchAgent is `com.hive.local-chief`; it starts at login only when local is
selected. The remote wrapper also checks the selection before any network
work. Menu pause/resume, status, logs and Open dashboard follow the selection.
The remote and local workers have separate pause/status files and chief rosters.

Switching waits up to an hour for a task to drain. If draining times out, the
old runner stays draining and no new worker starts; retry the command when the
task finishes. If startup fails after draining, the old runner stays paused;
the selected chief is visibly offline. Fix the reported error and retry, or
switch back. A lock prevents concurrent switches.

First local startup builds the dashboard (requires Node/npm) before draining
anything. The local chief uses Hive's saved configuration and installed GitHub
access; API-driven planning still needs the appropriate configured provider.
Startup logs: `~/Library/Logs/hive/local-chief.log`. Local worker logs:
`~/.local/share/hive-local/local-runner.log`.
