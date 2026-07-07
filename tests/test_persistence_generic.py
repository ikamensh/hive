"""hive.persistence as a generic package: models the store has never heard of.

The genericization contract: any Pydantic model with a string `id` works,
collection names derive from the class name (declared `__collection__` wins),
docs written before a field existed match filters for that field's declared
default, and CachedStore hydrates lazily per collection from its backing
store. Hive's own models exercise none of these edges directly, so they are
pinned here with foreign models.
"""

from pydantic import BaseModel, Field

from hive.persistence import CachedStore, FileStore, MemoryStore, collection_name


class GardenGnome(BaseModel):
    id: str
    mood: str = "cheerful"
    height_cm: int = 30


class Fungus(BaseModel):
    __collection__ = "fungi"

    id: str
    edible: bool = False


def test_collection_names_derive_and_respect_declarations():
    assert collection_name(GardenGnome) == "garden_gnomes"
    assert collection_name(Fungus) == "fungi"


def test_foreign_model_round_trip_memory_and_file(tmp_path):
    for store in (MemoryStore(), FileStore(tmp_path / "data")):
        store.put(GardenGnome(id="g1", mood="grumpy"))
        store.put(Fungus(id="f1", edible=True))
        assert store.get(GardenGnome, "g1").mood == "grumpy"
        assert [f.id for f in store.list(Fungus, edible=True)] == ["f1"]

    reloaded = FileStore(tmp_path / "data")  # collections discovered from disk
    assert reloaded.get(GardenGnome, "g1").mood == "grumpy"
    assert (tmp_path / "data" / "fungi" / "f1.json").exists()


def test_missing_key_matches_declared_default_only():
    """A doc written before `mood` existed behaves as if it holds the model's
    default — but never a dynamic (factory) default, which differs per row."""

    class Sighting(BaseModel):
        id: str
        certainty: str = "dubious"
        tag: str = Field(default_factory=lambda: "generated")

    store = MemoryStore()
    store._collection(collection_name(Sighting))["old"] = {"id": "old"}  # pre-schema doc

    assert [s.id for s in store.list(Sighting, certainty="dubious")] == ["old"]
    assert store.list(Sighting, certainty="confirmed") == []
    assert store.list(Sighting, tag="generated") == []  # factory defaults never back-fill


def test_cached_store_hydrates_lazily_and_writes_through():
    backing = MemoryStore()
    backing.put(GardenGnome(id="g1"))
    cache = CachedStore(backing)

    assert cache.get(GardenGnome, "g1").mood == "cheerful"  # hydrated on first touch
    cache.update(GardenGnome, "g1", lambda g: setattr(g, "mood", "stoic"))
    assert backing.get(GardenGnome, "g1").mood == "stoic"  # write-through

    cache.put(Fungus(id="f9"))
    assert backing.get(Fungus, "f9") is not None
    assert cache.get(Fungus, "f9") is not None
