"""CLI regressions discovered while exercising operator workflows."""

import subprocess
import sys

import pytest

from test_cli import cli, harness as harness


@pytest.mark.parametrize("bad_input", ["missing", "directory", "invalid-utf8"])
@pytest.mark.parametrize("command", ["plan-check", "plan-import", "new"])
def test_unreadable_input_fails_before_creating_a_project(harness, tmp_path, command, bad_input):
    """A local input mistake names the file, exits clearly, and creates no project."""
    client, _ = harness
    source = tmp_path / "plan.md"
    if bad_input == "directory":
        source.mkdir()
    elif bad_input == "invalid-utf8":
        source.write_bytes(b"\xff")
    argv = {
        "plan-check": ["plan-check", str(source)],
        "plan-import": ["plan-import", "demo", str(source), "--repo", "https://github.com/o/r.git"],
        "new": ["new", "demo", "--spec", str(source), "--repo", "https://github.com/o/r.git"],
    }[command]
    with pytest.raises(SystemExit, match="Cannot read input") as error:
        cli(client, *argv)
    assert str(source) in str(error.value)
    assert cli(client, "projects") == []


def test_offline_missing_plan_reports_filename_without_traceback(tmp_path):
    """The console entrypoint presents an expected filesystem error without a stack trace."""
    source = tmp_path / "missing.toml"
    result = subprocess.run(
        [sys.executable, "-m", "hive.cli", "plan-check", str(source)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert str(source) in result.stderr
    assert "Traceback" not in result.stderr


def test_id_shaped_project_name_resolves_for_read_and_write(harness):
    """Names that happen to look like IDs still address the project listed to the user."""
    client, _ = harness
    project = client.post("/api/projects", json={"name": "deadbeef1234"}).raise_for_status().json()
    assert cli(client, "project", project["name"])["project"]["id"] == project["id"]
    cli(client, "set", project["name"], "--paused", "true")
    assert cli(client, "project", project["id"])["project"]["paused"] is True


def test_archived_project_id_remains_accessible_without_shadowing_active_names(harness):
    """Archived projects keep stable ID access while visible names select current projects."""
    client, _ = harness
    archived = client.post("/api/projects", json={"name": "demo"}).raise_for_status().json()
    client.patch(f"/api/projects/{archived['id']}", json={"archived": True}).raise_for_status()
    active = client.post("/api/projects", json={"name": "demo"}).raise_for_status().json()
    assert cli(client, "project", archived["id"])["project"]["archived"] is True
    assert cli(client, "project", "demo")["project"]["id"] == active["id"]
    client.patch(f"/api/projects/{active['id']}", json={"archived": True}).raise_for_status()
    with pytest.raises(SystemExit, match="names 2 projects; pass the id") as error:
        cli(client, "project", "demo")
    assert all(project["id"] in str(error.value) for project in (active, archived))


@pytest.mark.parametrize("actions", [
    ["-m", "Use PostgreSQL instead", "--proceed"],
    ["-m", "Use PostgreSQL instead", "--approve"],
    ["--proceed", "--approve"],
])
def test_conflicting_intake_actions_cannot_start_or_advance_work(harness, actions, capsys):
    """A correction must never be silently discarded in favor of an approval flag."""
    client, _ = harness
    project = client.post("/api/projects", json={"name": "demo"}).raise_for_status().json()
    with pytest.raises(SystemExit) as error:
        cli(client, "intake", project["id"], *actions)
    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    detail = cli(client, "project", project["id"])
    assert detail["conversations"] == []
    assert detail["tasks"] == []


@pytest.mark.parametrize("budget", ["-1", "nan", "inf", "-inf"])
@pytest.mark.parametrize("command", ["new", "set"])
def test_invalid_budget_fails_before_mutating_projects(harness, command, budget, capsys):
    """Invalid spend caps cannot leave a half-created project or change existing settings."""
    client, _ = harness
    project = client.post("/api/projects", json={"name": "existing"}).raise_for_status().json()
    before = cli(client, "projects")
    argv = (["new", "bad-budget", "--repo", "https://github.com/o/r.git", f"--budget={budget}"]
            if command == "new" else ["set", project["id"], f"--daily-budget={budget}"])
    with pytest.raises(SystemExit) as error:
        cli(client, *argv)
    assert error.value.code == 2
    assert "finite nonnegative number" in capsys.readouterr().err
    assert cli(client, "projects") == before
