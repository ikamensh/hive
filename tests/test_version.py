import subprocess

from hive.version import (
    BASE_MAJOR,
    BASE_MINOR,
    VersionInfo,
    git_version,
    select_version,
    write_fallback,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo, name, content):
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", name)


def test_git_version_uses_commit_count_as_patch(tmp_path):
    """Every committed tree should advance the visible patch version."""
    _git(tmp_path, "init", "-b", "main")
    _commit(tmp_path, "one.txt", "one")
    _commit(tmp_path, "two.txt", "two")

    info = git_version(tmp_path)
    sha = _git(tmp_path, "rev-parse", "--short=7", "HEAD")

    assert info == VersionInfo(BASE_MAJOR, BASE_MINOR, 2, commit=sha, source="git")
    assert info.version == f"{BASE_MAJOR}.{BASE_MINOR}.2+{sha}"


def test_git_version_marks_dirty_tracked_trees(tmp_path):
    """A synced or edited tree should advertise that it is not exactly a commit."""
    _git(tmp_path, "init", "-b", "main")
    _commit(tmp_path, "one.txt", "one")
    (tmp_path / "one.txt").write_text("changed")

    info = git_version(tmp_path)

    assert info.dirty is True
    assert info.version.endswith(".dirty")


def test_generated_fallback_can_beat_stale_git_metadata():
    """Fast deploys rsync source without .git, so the stamped fallback may be newer."""
    git = VersionInfo(BASE_MAJOR, BASE_MINOR, 12, commit="old", source="git")
    fallback = VersionInfo(BASE_MAJOR, BASE_MINOR, 13, commit="new", source="generated-fallback")

    assert select_version(git, fallback) is fallback


def test_write_fallback_can_be_stamped_from_env(tmp_path, monkeypatch):
    """Packaging/deploy code can carry a computed version into a git-less tree."""
    monkeypatch.setenv("HIVE_VERSION", f"{BASE_MAJOR}.{BASE_MINOR}.77+abc1234")
    path = tmp_path / "_version_fallback.py"

    info = write_fallback(path)

    assert info.version == f"{BASE_MAJOR}.{BASE_MINOR}.77+abc1234"
    assert f'__version__ = "{info.version}"' in path.read_text()
