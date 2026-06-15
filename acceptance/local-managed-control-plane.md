# story: local-managed-control-plane [cli]
As an operator I can start Hive locally against managed state so that my MacBook can safely take over the control plane without forking runtime data.

## Rules
- Hive exposes a storage doctor that proves Firestore, GCS, workspace bootstrap, runner token presence, and leader lease status before startup.
- Starting the production control plane requires both `HIVE_GCP_PROJECT` and `HIVE_GCS_BUCKET`; missing managed-state configuration fails before startup with actionable output.
- A local control plane connected to the same workspace shows the same projects, resources, traces, org context, and human todos as the VM control plane.
- A second live control plane against the same workspace is refused by the leader lease instead of running concurrently.

## Examples
- Given the VM control plane is stopped or its leader lease has expired
  When I run the storage doctor and then start Hive locally with the managed-state environment set
  Then the API starts on localhost and reports the configured Firestore project, GCS bucket, workspace, auth mode, public URL, and runner autostart state
- Given `HIVE_GCP_PROJECT` is set but `HIVE_GCS_BUCKET` is missing
  When I start the production control plane
  Then startup fails before serving traffic and tells me the managed-state values I need to set
- Given another control plane still owns the workspace leader lease
  When I try to start a local control plane against that workspace
  Then startup fails with the current leader information and no second supervisor begins dispatching work
