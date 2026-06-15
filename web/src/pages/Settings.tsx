import { api, usePoll } from "../api";
import type { StorageInfo } from "../types";

function storageBackendLabel(storage: StorageInfo): string {
  if (storage.backend === "firestore") return "Managed (Firestore)";
  if (storage.backend === "file") return "Legacy local files";
  return "In-memory test store";
}

export default function Settings() {
  const { data, failed } = usePoll(() => api.storage(), []);

  const docTotal = data ? Object.values(data.counts).reduce((sum, n) => sum + n, 0) : 0;

  return (
    <div className="page page-settings">
      <div className="page-head">
        <h1>Settings</h1>
      </div>
      {!data ? (
        <p className="muted">{failed ? "unreachable" : "loading…"}</p>
      ) : (
        <section className="storage-panel">
          <h2 className="col-title">persistence</h2>
          <p className={data.fully_managed ? "storage-export-ok" : "form-error"}>
            {data.fully_managed ? "managed state active" : "not a managed runtime store"}
          </p>
          <div className="storage-grid">
            <div>
              <p className="storage-label">project data</p>
              <p className="storage-value">{storageBackendLabel(data)}</p>
              {data.store_path && <code className="storage-path">{data.store_path}</code>}
              {data.gcp_project && <code className="storage-path">{data.gcp_project}</code>}
              {data.backend === "file" && (
                <p className="muted">{docTotal} document{docTotal === 1 ? "" : "s"} on disk</p>
              )}
            </div>
            <div>
              <p className="storage-label">blobs (traces, orchestrator context)</p>
              <p className="storage-value">{data.blob_backend === "gcs" ? "Managed (GCS)" : "Local files"}</p>
              {data.blob_path && <code className="storage-path">{data.blob_path}</code>}
              {data.gcs_bucket && <code className="storage-path">{data.gcs_bucket}</code>}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
