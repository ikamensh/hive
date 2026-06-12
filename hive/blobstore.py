"""Blob persistence for orchestrator context and task traces."""

from __future__ import annotations

from pathlib import Path


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def get(self, name: str) -> bytes | None:
        path = self.root / name
        return path.read_bytes() if path.exists() else None

    def put(self, name: str, data: bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class GcsBlobStore:
    def __init__(self, bucket: str) -> None:
        from google.cloud import storage

        self.bucket = storage.Client().bucket(bucket)

    def get(self, name: str) -> bytes | None:
        blob = self.bucket.blob(name)
        return blob.download_as_bytes() if blob.exists() else None

    def put(self, name: str, data: bytes) -> None:
        self.bucket.blob(name).upload_from_string(data)
