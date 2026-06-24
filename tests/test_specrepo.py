"""SpecRepo round-trip against a local bare git repo (no network)."""

import subprocess

import pytest

from hive.integrations._specrepo import SpecRepo, authed_url, spec_status_dir


@pytest.fixture
def bare_repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True, capture_output=True)
    (seed / "mission.md").write_text("# Mission\nBuild the thing.")
    (seed / "iteration.md").write_text("# Iteration 1\nStep one.")
    subprocess.run(["git", "add", "-A"], cwd=seed, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed"],
        cwd=seed, check=True, capture_output=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True, capture_output=True)
    return origin


def test_sync_digest_commit_roundtrip(bare_repo, tmp_path):
    spec = SpecRepo(str(bare_repo), tmp_path / "work")
    spec.sync()
    digest = spec.digest()
    assert "Build the thing" in digest and "Step one" in digest

    sha = spec.commit_files(
        {"wiki/decisions.md": "## D1\nUse sqlite.", "input-log/0001.md": "raw answer"},
        "distill: D1",
    )
    assert len(sha) == 40

    # A second SpecRepo (fresh clone) sees the committed files: push worked.
    spec2 = SpecRepo(str(bare_repo), tmp_path / "work2")
    spec2.sync()
    assert "Use sqlite" in spec2.digest()
    assert "raw answer" in spec2.digest()


def test_sync_picks_up_remote_changes(bare_repo, tmp_path):
    spec = SpecRepo(str(bare_repo), tmp_path / "work")
    spec.sync()
    other = SpecRepo(str(bare_repo), tmp_path / "other")
    other.commit_files({"iteration.md": "# Iteration 1\nRevised."}, "revise")
    spec.sync()
    assert "Revised" in spec.digest()


def test_oversized_digest_raises(tmp_path):
    from hive.integrations._specrepo import MAX_DIGEST_CHARS, digest_dir

    (tmp_path / "mission.md").write_text("x" * (MAX_DIGEST_CHARS + 1))
    with pytest.raises(RuntimeError, match="distill the wiki"):
        digest_dir(tmp_path)


def test_authed_url():
    assert (
        authed_url("https://github.com/a/b.git", "tok")
        == "https://x-access-token:tok@github.com/a/b.git"
    )
    assert authed_url("/local/path", "tok") == "/local/path"
    assert authed_url("https://github.com/a/b.git", "") == "https://github.com/a/b.git"


def test_spec_status_requires_mission_and_iteration(tmp_path):
    status = spec_status_dir(tmp_path)
    assert not status.ready
    assert status.missing_files == ("mission.md", "iteration.md")

    (tmp_path / "mission.md").write_text("# Mission\nBuild the thing.\n")
    status = spec_status_dir(tmp_path)
    assert not status.ready
    assert status.present_files == ("mission.md",)
    assert status.missing_files == ("iteration.md",)

    (tmp_path / "iteration.md").write_text("# Iteration\nFirst loop.\n")
    status = spec_status_dir(tmp_path)
    assert status.ready
    assert status.missing_files == ()
