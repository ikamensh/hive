"""Human plan files retain task order and content through the CLI and API."""

from test_plans import app as app, FakeSpecRepo


def test_invalid_document_does_not_create_a_project(app, tmp_path):
    """A malformed file fails before any project or plan writes."""
    import pytest
    from test_cli import cli
    from hive.models import Project
    client, store = app
    source = tmp_path / "invalid.md"
    source.write_text("An idea, but no task headings")
    with pytest.raises(ValueError, match="task headings"):
        cli(client, "plan-import", "demo", str(source), "--repo", "https://github.com/o/r.git")
    assert not store.list(Project)


def test_markdown_import_creates_included_project_and_can_start(app, tmp_path, monkeypatch):
    """One file creates an ordered queue without an intake conversation or LLM call."""
    from test_cli import cli
    from hive.models import Task, TaskStatus, AgentConversation, Project
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    source = tmp_path / "tasks.md"
    source.write_text("# Improve the tool\n\nKeep changes small.\n\n## First\nDo A.\n\n```md\n## not a task\n```\n\n## Second\nDepends on A.\n")
    imported = cli(client, "plan-import", "demo", str(source), "--repo", "https://github.com/o/r.git",
                   "--validate", "uv run pytest tests/")
    assert [i["title"] for i in imported["items"]] == ["First", "Second"]
    assert "## not a task" in imported["items"][0]["notes"]
    assert "Keep changes small." in imported["plan"]["goal"]
    assert not store.list(Task) and not store.list(AgentConversation)
    project = store.list(Project)[0]
    assert project.included_only and project.daily_budget_usd == 0
    assert project.validation_command == "uv run pytest tests/"
    cli(client, "set", "demo", "--validate", "make check")
    assert store.get(Project, project.id).validation_command == "make check"
    cli(client, "plan-approve", "demo")
    assert len(store.list(Task, status=TaskStatus.pending)) == 1


def test_append_markdown_preserves_existing_live_items(app, tmp_path, monkeypatch):
    """Appending to a running queue preserves prior items and requires explicit approval."""
    from test_cli import cli
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    source = tmp_path / "tasks.md"
    source.write_text("# Goal\n\n## A\nBuild A.\n")
    first = cli(client, "plan-import", "demo", str(source), "--repo", "https://github.com/o/r.git", "--start")
    source.write_text("# Goal\n\n## B\nBuild B.\n")
    appended = cli(client, "plan-import", "demo", str(source), "--append")
    assert appended["items"][0]["id"] == first["items"][0]["id"]
    assert appended["items"][1]["status"] == "proposed"
