"""Typed document + blob persistence — usable on its own, no other hive imports.

Documents are your own Pydantic models (any class with a string ``id`` field);
collections derive from class names. One `StoreBase` contract, three
implementations (`MemoryStore`, `FileStore`, `FirestoreStore`), plus
`CachedStore`, a write-through cache for a single-writer process. Stores also
carry per-scope coordination singletons: a context blob and a TTL leader lease
(`claim_leader`) for fencing out a second writer. Blobs are plain bytes by key
(`LocalBlobStore`, `GcsBlobStore`).

Demos: `demos/persistence/`.
"""

from hive.persistence.blobstore import GcsBlobStore, LocalBlobStore
from hive.persistence.store import (
    CachedStore,
    FileStore,
    FirestoreStore,
    MemoryStore,
    StoreBase,
    collection_name,
)

__all__ = [
    "CachedStore",
    "FileStore",
    "FirestoreStore",
    "GcsBlobStore",
    "LocalBlobStore",
    "MemoryStore",
    "StoreBase",
    "collection_name",
]
