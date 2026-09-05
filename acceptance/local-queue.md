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
- `hive --local plan-import <project> <file> --repo <url>` accepts Markdown
  (`# goal`, ordered `## tasks`, bodies as instructions), creates an included-only
  project if missing, and does not run intake or an API planner. Approval starts
  the queue; `--start` approves on import. `--append` adds amendable proposals.
- `hive --local` commands never fall through to a configured remote chief.
- A configured `--validate` command is run by the runner after review. Landing
  requires a zero exit code on a clean, pushed commit and merges that exact SHA.
  Missing evidence, failing checks, dirty files, and unpushed edits block landing.
  Command, bounded output, exit code, and the passing SHA persist on the task.
- Quota exhaustion waits for capacity and resumes the interrupted build or
  review. A returning runner resumes its interrupted stage, including after a
  chief restart. Each retry has its own task record; late results are ignored.
- Retrying an interrupted stage preserves the original runner's checkout,
  including unpushed commits and dirty files. Other tasks cannot reset it while
  the retry waits. Provider flakes have bounded retries; actual build failures
  and operator cancellations still park for attention.

## Examples
- Given no GCP configuration, when I run `hive run --local`, then the chief and
  runner start and the startup output names the local state directory.
- Given a local project, when I stop and restart the chief using the same data
  directory, then the project and the runner's identity are preserved.
- Given a chief holding the workspace lease, when another chief attempts to
  claim it, then the second chief refuses to run until leadership is released.
