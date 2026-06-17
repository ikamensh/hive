import subprocess

from hive.runner.backends import PROBE_MARKER
from hive.runner.daemon import validate_probe_result


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
