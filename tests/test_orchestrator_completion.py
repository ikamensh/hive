"""Completion receives current review evidence even with obsolete durable history.

This checks context and real spec writes, not scripted-model factual judgment;
actual prompt compliance requires a separate live planner check.
"""

import pytest
from fastapi.testclient import TestClient

from hive._control.orchestrator import Orchestrator
from hive._control.supervisor import Supervisor
from hive._integrations.specrepo import SpecRepo
from hive.llm import Completion, ToolCall
from hive.models import (
    Plan, PlanItem, PlanItemStatus, PlanStatus, Project, Task, TaskKind,
    TaskStatus, ValidationResult, Verdict,
)
from hive.persistence import LocalBlobStore, MemoryStore
from test_llm import FakeAdapter, _config
from test_specrepo import bare_repo  # noqa: F401


@pytest.mark.parametrize("new_goal", ["", "Ship the next iteration"])
def test_completion_preserves_operator_edits_during_planning(
    bare_repo, tmp_path, monkeypatch, new_goal,  # noqa: F811 -- imported pytest fixture
):
    """A planner finishing the old iteration must preserve settings changed
    through the API while it thought, and a new goal must prevent completion."""
    from hive.api import create_app

    store = MemoryStore()
    project = store.put(Project(name="factory", spec_repo=str(bare_repo)))
    store.put(Plan(project_id=project.id, goal="Ship first iteration", status=PlanStatus.complete))
    config = _config(data_dir=tmp_path)
    client = TestClient(create_app(store, Supervisor(store, lambda *_: None), config))

    class OperatorEditAdapter(FakeAdapter):
        def start(self, *args):
            super().start(*args)
            patch = {"name": "renamed factory", "paused": True}
            if new_goal:
                patch["new_iteration_note"] = new_goal
            response = client.patch(f"/api/projects/{project.id}", json=patch)
            assert response.status_code == 200

    adapter = OperatorEditAdapter([
        Completion(tool_calls=[ToolCall(name="mark_goal_complete", arguments={"summary": "Try it: run"})]),
        Completion(text="Finished reviewing the old iteration."),
    ])
    adapter.model = "scripted"
    orch = Orchestrator(store, LocalBlobStore(tmp_path / "blobs"), config)
    monkeypatch.setattr(orch, "_build_adapters", lambda: [adapter])

    orch.invoke(project.id, ["Iteration plan complete; summarize delivered scope."])

    saved = client.get(f"/api/projects/{project.id}").json()["project"]
    assert saved["name"] == "renamed factory"
    assert saved["paused"] is True
    assert saved["pending_iteration_goal"] == new_goal
    assert saved["goal_complete"] is (not new_goal)
    result = adapter.fed[0][0].content
    if new_goal:
        assert "awaits a plan" in result
    else:
        assert result == "goal marked complete"


def test_completion_receives_current_accepted_evidence_and_preserves_history(
    bare_repo, tmp_path, monkeypatch,  # noqa: F811 -- imported pytest fixture
):
    store = MemoryStore()
    project = store.put(Project(name="factory", spec_repo=str(bare_repo)))
    plan = store.put(Plan(project_id=project.id, goal="Run the whole factory"))
    item = store.put(PlanItem(
        project_id=project.id, plan_id=plan.id, title="Production game",
        status=PlanItemStatus.reviewing,
    ))
    old = store.put(Task(
        id="old-review", project_id=project.id, workstream_id="old-item", repo=str(bare_repo),
        kind=TaskKind.review, instructions="Review the first engine", created_at=1,
        status=TaskStatus.done, verdict=Verdict.accept, result_text="Engine contract 2.",
        validation=ValidationResult(command="make check", exit_code=0, commit_sha="1" * 40),
    ))
    spec = SpecRepo(str(bare_repo), tmp_path / "seed-spec")
    spec.commit_files({"wiki/landed.md": "Engine contract 2."}, "Record early increment")
    first = FakeAdapter([Completion(text="The earlier increment used engine contract 2.")])
    first.model = "scripted"
    orch = Orchestrator(store, LocalBlobStore(tmp_path / "blobs"), _config(data_dir=tmp_path))
    monkeypatch.setattr(orch, "_build_adapters", lambda: [first])
    orch.invoke(project.id, ["Earlier increment landed; production game still reviewing."])

    rejected = store.put(old.model_copy(update={
        "id": "rejected-review", "workstream_id": item.id, "created_at": 2,
        "verdict": Verdict.reject, "result_text": "Production game is incomplete.",
        "validation": ValidationResult(command="make check", exit_code=0, commit_sha="2" * 40),
    }))
    accepted = store.put(old.model_copy(update={
        "id": "accepted-review", "workstream_id": item.id, "created_at": 3,
        "result_text": "Production game and packaging delivered. Engine contract 3. 508 checks passed.",
        "validation": ValidationResult(command="make check", exit_code=0, commit_sha="3" * 40),
    }))
    item.status = PlanItemStatus.done
    plan.status = PlanStatus.complete
    store.put(item)
    store.put(plan)
    summary = "Production game and packaging delivered. Try it: moldsim serve. 508 checks passed."
    final = FakeAdapter([
        Completion(tool_calls=[ToolCall(name="commit_to_spec", arguments={
            "files_json": '{"wiki/landed.md": "Production game and packaging delivered."}',
            "message": "Record delivered scope",
        })]),
        Completion(tool_calls=[ToolCall(name="mark_goal_complete", arguments={"summary": summary})]),
        Completion(text="Recorded completion."),
    ])
    final.model = "scripted"
    monkeypatch.setattr(orch, "_build_adapters", lambda: [final])
    orch.invoke(project.id, ["Iteration plan complete; summarize delivered scope."])

    _, history, context, _ = final.started
    assert any("engine contract 2" in message["text"] for message in history)
    assert "=== wiki/landed.md ===\nEngine contract 2." in context
    # Review and validation are separate facts: passing tests did not accept
    # the rejected attempt, and both historical SHAs remain attributable.
    for task in (old, rejected, accepted):
        row = context.split(f"task {task.id} ", 1)[1].split("\n- [", 1)[0]
        assert f"verdict={task.verdict}" in row
        assert task.result_text in row
        assert task.validation.command in row
        assert "exit_code=0" in row
        assert task.validation.commit_sha in row
    positions = [context.index(f"task {task.id}") for task in (old, rejected, accepted)]
    assert positions == sorted(positions)
    assert store.get(Project, project.id).goal_complete_note == summary
    fresh_spec = SpecRepo(str(bare_repo), tmp_path / "observer")
    fresh_spec.sync()
    assert (fresh_spec.path / "wiki/landed.md").read_text() == "Production game and packaging delivered."
    assert store.get(Task, old.id) == old
    assert store.get(Task, rejected.id) == rejected
