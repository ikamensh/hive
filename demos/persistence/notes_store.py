"""Demo: a notes app on your own models — `hive.persistence` standalone.

Task: you need durable, queryable storage for your own Pydantic models with
zero schema setup — plus mutation that survives concurrent writers. Define a
model, `put` it, filter with `list`, race 200 threads through `update`
without losing a single increment, and reload everything from disk.

    uv run python demos/persistence/notes_store.py

Offline: everything lands in a temp directory as plain JSON files.
"""

import tempfile
import threading
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from hive.persistence import FileStore, collection_name


class Note(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str
    body: str = ""
    pinned: bool = False
    views: int = 0
    created_at: float = 0.0


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "notes-data"
    store = FileStore(root)

    milk = store.put(Note(title="buy milk", created_at=1.0))
    store.put(Note(title="write demo", pinned=True, created_at=2.0))
    store.put(Note(title="ship it", pinned=True, created_at=3.0))
    print(f"collection on disk: {root / collection_name(Note)}")

    pinned = store.list(Note, pinned=True)
    print(f"pinned notes: {[n.title for n in pinned]}")

    # 20 threads x 10 atomic updates: `update` is read-modify-write under the
    # store's lock, so no increment is ever lost to a concurrent writer.
    def read_note_repeatedly():
        for _ in range(10):
            store.update(Note, milk.id, lambda n: setattr(n, "views", n.views + 1))

    threads = [threading.Thread(target=read_note_repeatedly) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"views after 20x10 concurrent bumps: {store.get(Note, milk.id).views}")
    assert store.get(Note, milk.id).views == 200

    # Durability: a fresh store over the same directory sees identical state.
    reloaded = FileStore(root)
    assert [n.title for n in reloaded.list(Note)] == [n.title for n in store.list(Note)]
    print(f"reloaded from disk: {len(reloaded.list(Note))} notes, views intact "
          f"({reloaded.get(Note, milk.id).views})")
