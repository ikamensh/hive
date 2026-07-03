"""Chief discovery roster + runner self-update trigger.

Roster properties (chosen to survive refactors):
- candidate order: last-known-good first, then configured seeds, then learned;
- state invariance under save/load: a reloaded roster behaves identically;
- merge is idempotent and normalizing (trailing slashes, duplicates, blanks);
- corrupt or missing state never breaks a roster seeded from env.

update_available properties (real local git repos, no network):
- a checkout even with origin/main sees no update;
- a new upstream commit is an update;
- any broken precondition (not a repo) reads as 'no update' — a fetch hiccup
  must never take a working runner down.
"""

import json
import subprocess

from hive.runner._chief_roster import ChiefRoster, parse_urls
from hive.runner._daemon import update_available


def test_parse_urls_normalizes():
    raw = " https://a.example/ ,, https://b.example, https://a.example"
    assert parse_urls(raw) == ["https://a.example", "https://b.example"]


def test_candidate_order_preferred_then_seeds_then_learned(tmp_path):
    roster = ChiefRoster(["https://seed1", "https://seed2"], tmp_path / "chiefs.json")
    roster.merge_advertised(["https://learned", "https://seed1/"])
    assert roster.candidates() == ["https://seed1", "https://seed2", "https://learned"]

    roster.mark_success("https://learned")
    assert roster.candidates()[0] == "https://learned"


def test_roster_state_survives_restart(tmp_path):
    state = tmp_path / "chiefs.json"
    first = ChiefRoster(["https://seed"], state)
    first.merge_advertised(["https://moved-chief"])
    first.mark_success("https://moved-chief")

    reborn = ChiefRoster(["https://seed"], state)
    assert reborn.candidates() == first.candidates()
    assert reborn.candidates()[0] == "https://moved-chief"


def test_merge_is_idempotent(tmp_path):
    roster = ChiefRoster(["https://seed"], tmp_path / "chiefs.json")
    roster.merge_advertised(["https://x"])
    before = roster.candidates()
    roster.merge_advertised(["https://x/", "https://seed"])
    assert roster.candidates() == before


def test_corrupt_state_falls_back_to_seeds(tmp_path):
    state = tmp_path / "chiefs.json"
    state.write_text("{not json")
    roster = ChiefRoster(["https://seed"], state)
    assert roster.candidates() == ["https://seed"]
    # ...and recovers: the next successful save overwrites the garbage.
    roster.mark_success("https://seed")
    assert json.loads(state.read_text())["preferred"] == "https://seed"


def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin",
             "HOME": str(cwd)},
    )


def _repo_pair(tmp_path):
    """An 'origin' repo with one commit on main, and a clone of it."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-b", "main", cwd=origin)
    (origin / "f.txt").write_text("v1")
    _git("add", ".", cwd=origin)
    _git("commit", "-m", "v1", cwd=origin)
    clone = tmp_path / "clone"
    _git("clone", str(origin), str(clone), cwd=tmp_path)
    return origin, clone


def test_update_available_detects_upstream_commit(tmp_path):
    origin, clone = _repo_pair(tmp_path)
    assert update_available(clone) is False  # even with origin/main

    (origin / "f.txt").write_text("v2")
    _git("commit", "-am", "v2", cwd=origin)
    assert update_available(clone) is True


def test_update_available_never_raises_outside_a_repo(tmp_path):
    assert update_available(tmp_path / "not-a-repo") is False
