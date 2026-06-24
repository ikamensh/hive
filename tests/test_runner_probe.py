import subprocess

from hive.runner._backends import PROBE_MARKER
from hive.runner._daemon import ensure_probe_repo, validate_probe_result


def _porcelain(path):
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True
    ).stdout.strip()


def test_ensure_probe_repo_builds_clean_repo_and_self_cleans(tmp_path, monkeypatch):
    """The runner builds its own probe repo (no chief path, so remote
    runners can probe), idempotently, and resets it clean each time so a prior
    probe's mess never fails the next."""
    import hive.runner._daemon as daemon

    monkeypatch.setattr(daemon, "WORKDIR", tmp_path)

    repo = ensure_probe_repo()
    assert (repo / ".git").exists()
    assert _porcelain(repo) == ""  # fresh repo is clean
    text, is_error = validate_probe_result(repo, PROBE_MARKER, False)
    assert not is_error and "HIVE PROBE PASSED" in text

    # A dirty tree (as a misbehaving probe might leave) is restored on next build.
    (repo / "scratch.txt").write_text("junk left by a probe")
    again = ensure_probe_repo()
    assert again == repo
    assert _porcelain(repo) == ""


def _git_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("probe repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True)


def test_probe_result_requires_marker_and_clean_repo(tmp_path):
    _git_repo(tmp_path)

    text, is_error = validate_probe_result(tmp_path, PROBE_MARKER, False)
    assert not is_error
    assert "HIVE PROBE PASSED" in text

    text, is_error = validate_probe_result(tmp_path, "hello", False)
    assert is_error
    assert "marker" in text

    (tmp_path / "README.md").write_text("dirty\n")
    text, is_error = validate_probe_result(tmp_path, PROBE_MARKER, False)
    assert is_error
    assert "left repository changes" in text
