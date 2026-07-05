# story: local-managed-chief [ui]
As an operator I can start Hive's control plane against managed state so that a local or VM chief sees one shared runtime truth.

## Rules
- Hive exposes a storage doctor that proves Firestore, GCS, workspace bootstrap, runner token presence, and leader lease status before startup.
- Starting the production chief requires both `HIVE_GCP_PROJECT` and `HIVE_GCS_BUCKET`; missing managed-state configuration fails before startup with actionable output.
- Starting the chief prints the active Firestore project, GCS bucket, workspace, auth mode, public URL, and runner autostart state.
- A chief connected to the same workspace shows the same projects, resources, traces, org context, and human todos whether it is running locally or on the VM.
- `hive projects`, `hive resources`, and the web Resources page report the same managed-state projects, runners, backend resources, cooldowns, and human todos.
- If the local API is unreachable, the CLI and web UI show a clear unreachable or error state instead of stale successful data.
- A second live chief against the same workspace is refused by the leader lease instead of running concurrently.
- A restarted chief or runner recovers gracefully from persisted state: the restarted chief reloads projects, tasks, and resources, in-flight tasks are requeued or failed with an event, and the orchestrator history can cold-start from the spec digest.

## Examples
- Given the VM chief is stopped or its leader lease has expired
  When I run the storage doctor and then start Hive locally with the managed-state environment set
  Then the API starts on localhost and reports the configured Firestore project, GCS bucket, workspace, auth mode, public URL, and runner autostart state
- Given the local API is running against the managed workspace
  When I run `hive projects`, run `hive resources`, and open the Resources page
  Then all three surfaces show the same projects, runners, resource status, cooldowns, and human todos
- Given `HIVE_GCP_PROJECT` is set but `HIVE_GCS_BUCKET` is missing
  When I start the production chief
  Then startup fails before serving traffic and tells me the managed-state values I need to set
- Given another chief still owns the workspace leader lease
  When I try to start a local chief against that workspace
  Then startup fails with the current leader information and no second supervisor begins dispatching work
- Given the chief process is restarted while tasks are in-flight
  When the chief boots up
  Then it reloads all projects, tasks, and resources from the managed state, requeues or fails in-flight tasks with a system event, and cold-starts its orchestrator from the spec digest
