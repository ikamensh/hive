import { useState } from "react";
import { api, usePoll } from "../api";
import type { StorageInfo } from "../types";

function storageBackendLabel(storage: StorageInfo): string {
  if (storage.backend === "firestore") return "Cloud (Firestore)";
  if (storage.backend === "file") return "Local files";
  return "In-memory (not persisted)";
}

export default function Storage() {
  const { data, failed } = usePoll(() => api.storage(), []);
  const [gcpProject, setGcpProject] = useState("");
  const [gcsBucket, setGcsBucket] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const exportToGcp = async () => {
    if (!gcpProject.trim()) return;
    setExporting(true);
    setExportMessage(null);
    setExportError(null);
    try {
      const result = await api.exportStorage({
        gcp_project: gcpProject.trim(),
        gcs_bucket: gcsBucket.trim() || undefined,
      });
      setExportMessage(result.message);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "export failed");
    } finally {
      setExporting(false);
    }
  };

  const docTotal = data ? Object.values(data.counts).reduce((sum, n) => sum + n, 0) : 0;

  return (
    <div className="page page-storage">
      <div className="page-head">
        <h1>Storage</h1>
      </div>
      {!data ? (
        <p className="muted">{failed ? "unreachable" : "loading…"}</p>
      ) : (
        <section className="storage-panel">
          <h2 className="col-title">backends</h2>
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
              <p className="storage-value">{data.blob_backend === "gcs" ? "Cloud (GCS)" : "Local files"}</p>
              {data.blob_path && <code className="storage-path">{data.blob_path}</code>}
              {data.gcs_bucket && <code className="storage-path">{data.gcs_bucket}</code>}
            </div>
          </div>
          {data.export_available && (
            <div className="storage-export">
              <p className="muted">
                Copy local data to GCP when you are ready for cloud persistence. Requires Application Default Credentials
                (`gcloud auth application-default login`).
              </p>
              <div className="storage-export-form">
                <input
                  placeholder="GCP project (e.g. hive-ikamen)"
                  value={gcpProject}
                  onChange={(e) => setGcpProject(e.target.value)}
                />
                <input
                  placeholder="GCS bucket (optional)"
                  value={gcsBucket}
                  onChange={(e) => setGcsBucket(e.target.value)}
                />
                <button onClick={exportToGcp} disabled={exporting || !gcpProject.trim()}>
                  {exporting ? "exporting…" : "export to GCP"}
                </button>
              </div>
              {exportMessage && <p className="storage-export-ok">{exportMessage}</p>}
              {exportError && <p className="form-error">{exportError}</p>}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
