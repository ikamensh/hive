# Proposal: Managed State Only

## Decision

Hive runtime state should live in managed services by default and by requirement:

- Firestore for structured state.
- GCS for blobs: traces, orchestrator history, issue attachments, archives.
- Secret Manager or explicit local stored config for credentials.

Local file persistence should stop being a supported runtime mode. It can remain only for tests, offline unit fixtures, and one-time migration tools.

This keeps the VM and MacBook interchangeable as control-plane hosts: either one can start against the same managed state after the other leader stops, and both see the same projects, tasks, runners, resources, traces, org context, and human todos.

## Why

The current code lets `HIVE_GCP_PROJECT` and `HIVE_GCS_BUCKET` be configured independently. That creates a dangerous middle state: documents can be in Firestore while blobs stay under `HIVE_DATA_DIR/blobs` on whichever machine happened to run the control plane. A later VM or Mac launch then sees the Firestore documents but cannot read the local traces, attachments, or orchestrator history.

For Hive's actual goal, "local" should mean "the control plane process is running on my MacBook," not "state lives on my MacBook." Managed state is simpler, more honest, and closer to the deployment model.

## Scope

In scope:

- Control-plane structured state.
- Blob state.
- Storage selection, launch checks, CLI notes, UI storage display.
- Local-to-managed migration for existing file-backed data.
- Tests that need in-memory or file-backed stores.

Out of scope:

- Runner working directories and git checkouts. Those stay local and disposable.
- Agent CLI session files outside Hive's own persisted trace/history model.
- GCP Secret Manager as the only credential source for MacBook development. Stored local config is still acceptable for secrets, as long as state is managed.

## Required Behavior

1. `production_app()` must refuse to start without both `HIVE_GCP_PROJECT` and `HIVE_GCS_BUCKET`.
2. `hive run` must print the active Firestore project, GCS bucket, workspace, public URL, auth mode, and whether local runner autostart is enabled.
3. There should be no env-driven fallback from production runtime to `FileStore` or `LocalBlobStore`.
4. The web UI should never label Firestore-only as "cloud persistence." Full cloud persistence requires Firestore plus GCS.
5. The storage/export endpoint should either be removed from the production app or changed into an explicit migration-only path that cannot run once managed state is active.
6. Tests may still instantiate `MemoryStore`, `FileStore`, and `LocalBlobStore` directly.
7. A second control plane against the same workspace should continue to fail on the leader lease; interchangeable means clean handoff, not two live supervisors.

## Implementation Plan

### 1. Split Runtime Storage From Test Storage

Change storage construction so production has one path:

- `make_store(config)` returns `FirestoreStore` only when `config.gcp_project` is set; otherwise raises a clear configuration error.
- `make_blob_store(config)` returns `GcsBlobStore` only when `config.gcs_bucket` is set; otherwise raises a clear configuration error.
- Tests that need files instantiate `FileStore(tmp_path / "store")` directly.
- Tests that need blobs instantiate `LocalBlobStore(tmp_path / "blobs")` directly.

Do not use a broad `HIVE_ALLOW_LOCAL_STORE` escape hatch for normal runtime. If a temporary escape hatch is needed during the migration, make it migration-only and remove it after the local data is copied.

### 2. Make Launch Failures Useful

Update `hive run` and `Config.from_env` handling so missing cloud state fails with actionable output:

```text
Hive requires managed state.
Set:
  HIVE_GCP_PROJECT=hive-ikamen
  HIVE_GCS_BUCKET=hive-ikamen-blobs

Then run:
  gcloud auth application-default login
  uv run hive run
```

The launch summary should include:

- Firestore project and source.
- GCS bucket and source.
- Workspace ID and name.
- Auth mode.
- Public URL.
- Local runner autostart.
- Current leader failure message if another control plane owns the lease.

### 3. Add A Managed-State Doctor

Add `hive doctor storage` or `hive doctor distributed`:

- Reads and writes a temporary Firestore document.
- Uploads, reads, and deletes a temporary GCS blob.
- Confirms workspace bootstrap.
- Prints current leader lease holder.
- Confirms the configured runner token is present.
- Reports whether the current process is safe to start as control plane or should run only as a runner.

This should be the first command in the MacBook and VM handoff docs.

### 4. Keep FileStore As Test And Migration Code

Do not delete `FileStore` immediately. Keep it for:

- Unit tests.
- Regression tests around store semantics.
- A one-time local-to-managed migration command.

But remove it from production storage selection. The class can live in `hive.store`; the runtime should not choose it from env.

### 5. Replace Runtime Export With A Migration Command

Move local export out of the always-running API and into an explicit CLI or script:

```bash
uv run hive migrate-local-state \
  --data-dir ~/.hive-data \
  --gcp-project hive-ikamen \
  --gcs-bucket hive-ikamen-blobs
```

The migration should:

- Require all control planes to be stopped.
- Copy every collection.
- Copy org context for every workspace.
- Copy all local blobs.
- Verify document counts.
- Verify blob count and, preferably, hashes.
- Write a migration report.
- Never copy the old leader lease.

After migration, normal `hive run` should use only Firestore and GCS.

### 6. Update Docs And Current Iteration

Replace "run with local files for a throwaway run" with:

- Local process, managed state.
- For first setup, run `hive doctor storage`.
- For a MacBook control-plane handoff, stop the VM control plane or wait for the leader lease to expire.
- For normal distributed operation, keep the VM control plane running and start the MacBook only as a runner.

The README should say GCS is required for cloud persistence, not optional.

## Test Plan

Unit tests:

- `production_app` or runtime storage creation fails without `HIVE_GCP_PROJECT`.
- Runtime storage creation fails without `HIVE_GCS_BUCKET`.
- `make_store` no longer silently returns `FileStore` in production paths.
- `make_blob_store` no longer silently returns `LocalBlobStore` in production paths.
- Existing `MemoryStore` and `FileStore` contract tests still pass by constructing stores directly.
- Migration copies documents, org context, and blobs, and does not copy leader leases.

Integration-style tests:

- CLI launch summary includes Firestore, GCS, workspace, auth mode, and public URL.
- Storage doctor reports success with mocked Firestore/GCS clients.
- Mixed Firestore/local-blob configuration is rejected.

Manual preflight:

- `uv run pytest tests/`
- `hive doctor storage` from the MacBook.
- Stop VM control plane.
- Start MacBook control plane against Firestore/GCS.
- Confirm projects/resources/traces match.
- Stop MacBook control plane.
- Restart VM control plane.
- Confirm state continuity.

## Rollout

1. Add migration command and tests while current local export still exists.
2. Run migration for any local data worth keeping.
3. Configure MacBook stored config with `HIVE_GCP_PROJECT`, `HIVE_GCS_BUCKET`, `HIVE_WORKSPACE_ID`, and auth settings.
4. Change runtime storage constructors to require managed state.
5. Update README, iteration docs, and UI copy.
6. Remove or hide the API export endpoint from production.

## Open Questions

- Should MacBook control-plane credentials come from local stored config or a helper that pulls Secret Manager values into `~/.config/hive/config.env`?
- Should `hive run` automatically start a local runner after acquiring leadership, or should runner startup remain explicit for handoff clarity?
- Should the leader lease holder be exposed through an authenticated API endpoint, or only through the doctor command?

## Acceptance Criteria

- A normal `hive run` cannot create local state files.
- Firestore-only without GCS is impossible in production runtime.
- MacBook and VM control planes see identical state after a clean handoff.
- MacBook as runner against the VM control plane remains unchanged.
- Tests still use cheap in-memory/file stores without requiring GCP.
