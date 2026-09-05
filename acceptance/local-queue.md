# story: local-queue
As an operator I can run Hive on my laptop, submit an ordered plan, and let it
make reviewed progress across interruptions without configuring cloud storage.

## Rules
- `hive run --local` starts the chief and a local runner with persistent state
  under `~/.local/share/hive`, overridable with `--data-dir`.
- Local runner checkouts, logs, reconnect roster, and pause flag belong to this
  install. They do not reuse an installed fleet runner's state.
- Stopping the chief stops its runner. Restarting reopens the same projects.
- Only one chief may hold a local workspace's leader lease at a time.
- GitHub remains the landing destination for this iteration.

## Examples
- Given no GCP configuration, when I run `hive run --local`, then the chief and
  runner start and the startup output names the local state directory.
- Given a local project, when I stop and restart the chief using the same data
  directory, then the project and the runner's identity are preserved.
- Given a chief holding the workspace lease, when another chief attempts to
  claim it, then the second chief refuses to run until leadership is released.
