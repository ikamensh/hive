"""Plan CLI interaction and progress against the real chief/runner protocol."""

from test_api_e2e import _pump, _register_usable_runner
from test_cli import RUNNER_HEADERS, cli
from test_plans import FakeSpecRepo, app as app

from hive.cli import format_plan


def test_live_plan_view_and_amendments(app, tmp_path, monkeypatch):
    """Queued edits and approved appends reach subsequent agents; progress shows
    the actual task, repair feedback, validation, and landed work throughout."""
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    monkeypatch.setattr("hive.api.merge_branch", lambda *a, **kw: None)
    monkeypatch.setattr("hive.api.delete_branch", lambda *a, **kw: None)
    source = tmp_path / "tasks.md"
    source.write_text("# Production simulator\n\n## Core\nBuild the core.\n\n## Charts\nDraw charts.\n")
    initial = cli(client, "plan-import", "simulator", str(source), "--repo", "https://github.com/o/sim.git")
    cli(client, "set", "simulator", "--builder", "opencode=opencode/test-free",
        "--reviewer", "opencode=opencode/test-free", "--validate", "make check")
    cli(client, "plan-approve", "simulator")
    waiting = cli(client, "plan", "simulator")
    assert waiting["state_reason"] in format_plan(waiting)
    assert "0 landed" in format_plan(waiting)
    assert "1 queued" in format_plan(waiting)

    rid = _register_usable_runner(client, backend="opencode")

    def next_task():
        _pump(client, store)
        return client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).raise_for_status().json()["task"]

    def finish(task, text, validation=None):
        body = {"text": text}
        if validation is not None:
            body["validation"] = validation
        client.post(f"/api/tasks/{task['id']}/result", json=body,
                    headers=RUNNER_HEADERS).raise_for_status()

    build = next_task()
    rendered = format_plan(cli(client, "plan", "simulator"))
    assert build["id"] in rendered and "opencode/test-free" in rendered
    assert "running" in rendered and "elapsed" in rendered

    edited = cli(client, "plan-item-edit", initial["items"][1]["id"],
                 "--notes", "Show production throughput and bottlenecks.")
    assert edited["status"] == "queued"
    source.write_text("# Production simulator\n\n## Export\nExport scenario results.\n")
    appended = cli(client, "plan-import", "simulator", str(source), "--append", "--start")
    assert [i["status"] for i in appended["items"]] == ["resolving", "queued", "queued"]
    assert appended["items"][0]["id"] == initial["items"][0]["id"]

    finish(build, "Core implemented.\nOUTCOME: FIXED")
    review = next_task()
    finish(review, "Incorrect bottleneck calculation.\nREVIEW: REJECT", {
        "command": "make check", "exit_code": 1, "output": "bottleneck assertion failed",
    })
    repair = next_task()
    rendered = format_plan(cli(client, "plan", "simulator"))
    assert "repairs: 1" in rendered
    assert "Incorrect bottleneck calculation" in rendered
    assert "validation: FAIL" in rendered and "bottleneck assertion failed" in rendered
    finish(repair, "Corrected bottleneck calculation.\nOUTCOME: FIXED")
    finish(next_task(), "Reviewed the correction.\nREVIEW: ACCEPT", {
        "command": "make check", "exit_code": 0, "output": "all checks passed", "commit_sha": "a" * 40,
    })
    charts = next_task()
    assert "Show production throughput and bottlenecks." in charts["instructions"]
    rendered = format_plan(cli(client, "plan", "simulator"))
    assert "1 landed" in rendered and "1 queued" in rendered
    assert "validation: PASS" in rendered and "aaaaaaaaaaaa" in rendered


def test_plan_watch_emits_json_updates_and_stops_on_abandon(app, tmp_path, monkeypatch, capsys):
    """A piped watcher emits parseable snapshots as the real API changes and
    stops on a terminal plan without sending pause or cancellation itself."""
    import json
    from hive.cli import main

    client, _ = app
    source = tmp_path / "tasks.md"
    source.write_text("# Goal\n\n## First\nBuild it.\n")
    cli(client, "plan-import", "demo", str(source), "--repo", "https://github.com/o/r.git")
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)
    sleeps = []

    def abandon(interval):
        sleeps.append(interval)
        cli(client, "plan-abandon", "demo")

    monkeypatch.setattr("hive.cli.time.sleep", abandon)
    main(["--local", "plan", "demo", "--watch", "0.1", "--json"])
    snapshots = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [snapshot["plan"]["status"] for snapshot in snapshots] == ["draft", "abandoned"]
    assert sleeps == [0.1]
    assert client.get("/api/workspace").json()["paused"] is False

    cli(client, "plan-import", "demo", str(source))

    def interrupt(interval):
        raise KeyboardInterrupt

    monkeypatch.setattr("hive.cli.time.sleep", interrupt)
    main(["--local", "plan", "demo", "--watch"])
    assert "hive --local plan-item-edit" in capsys.readouterr().out
    assert cli(client, "plan", "demo")["plan"]["status"] == "draft"
    assert client.get("/api/workspace").json()["paused"] is False


def test_agent_preferences_round_trip_through_import_and_set(app, tmp_path):
    """Ordered capacity preferences persist and an unrelated import does not
    reset them; the operator can replace or clear the list explicitly."""
    client, _ = app
    source = tmp_path / "tasks.md"
    source.write_text("# Goal\n\n## First\nBuild it.\n")
    cli(client, "plan-import", "demo", str(source), "--repo", "https://github.com/o/r.git",
        "--prefer", "opencode=opencode/test-free", "--prefer", "codex")
    expected = [{"backend": "opencode", "model": "opencode/test-free"}, {"backend": "codex", "model": ""}]
    assert cli(client, "project", "demo")["project"]["agent_preferences"] == expected
    source.write_text("# Goal\n\n## Second\nExtend it.\n")
    cli(client, "plan-import", "demo", str(source), "--append")
    assert cli(client, "project", "demo")["project"]["agent_preferences"] == expected
    cli(client, "set", "demo", "--prefer", "codex", "--prefer", "claude=opus")
    assert cli(client, "project", "demo")["project"]["agent_preferences"] == [
        {"backend": "codex", "model": ""}, {"backend": "claude", "model": "opus"},
    ]
    cli(client, "set", "demo", "--prefer", "clear")
    assert cli(client, "project", "demo")["project"]["agent_preferences"] == []

    import pytest
    with pytest.raises(SystemExit, match="unknown preferred backend"):
        cli(client, "plan-import", "bad-choice", str(source), "--repo", "https://github.com/o/r.git",
            "--prefer", "typo")
    assert [project["name"] for project in cli(client, "projects")] == ["demo"]


def test_import_preferences_choose_first_agent_without_default_role_pins(app, tmp_path, monkeypatch):
    """A newly imported plan honors the explicit preference chain; hidden
    OpenCode/Codex role defaults must not jump ahead of its first choice."""
    client, _ = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    source = tmp_path / "tasks.md"
    source.write_text("# Goal\n\n## First\nBuild it.\n")
    plan = cli(client, "plan-import", "demo", str(source), "--repo", "https://github.com/o/r.git",
               "--prefer", "codex=gpt-6-astra", "--prefer", "opencode=opencode/test-free", "--start")
    project = cli(client, "project", "demo")["project"]
    assert project["build_backend"] == project["review_backend"] == ""
    assert [(task["backend"], task["model"]) for task in plan["tasks"]] == [("codex", "gpt-6-astra")]


def test_limits_cli_distinguishes_shared_and_model_specific_capacity(app):
    """A Fable limit must be displayed as Fable-only, with the attempted model
    and reset, while shared usage remains separately visible."""
    import time
    from hive.cli import format_show
    from hive.models import Task

    client, store = app
    now = time.time()
    snapshot = {"captured_at": now, "source": "oauth", "windows": [
        {"kind": "weekly_all", "model_scope": "", "used_percent": 10, "resets_at": now + 3600},
        {"kind": "weekly_fable", "model_scope": "fable", "used_percent": 100, "resets_at": now + 1800},
    ]}
    rid = client.post("/api/runners/register", headers=RUNNER_HEADERS, json={
        "name": "local", "backends": ["claude"], "usage_snapshots": {"claude": snapshot},
    }).raise_for_status().json()["runner_id"]
    project = client.post("/api/projects", json={"name": "demo"}).json()
    task = store.put(Task(project_id=project["id"], workstream_id="fixture", repo="https://github.com/o/r.git",
                          instructions="Finish the work", backend="claude", model="claude-fable-5-1",
                          runner_id=rid, status="running"))
    client.post(f"/api/tasks/{task.id}/result", headers=RUNNER_HEADERS, json={
        "text": "Fable usage limit reached", "is_error": True, "resource_exhausted": True,
        "usage_snapshot": snapshot,
    }).raise_for_status()
    rendered = format_show(cli(client, "show", "limits"), "limits")
    assert "scope: shared" in rendered and "scope: fable" in rendered
    assert "fable cooling down until" in rendered
    assert "model: claude-fable-5-1" in rendered
