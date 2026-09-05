"""OpenCode orchestration crosses the real process boundary, with Hive tools
executed only after the complete model turn passes schema validation."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hive.llm import ToolLoop, ToolSet, Usage, build_adapter, candidate_providers
from test_llm import _config


def executable(tmp_path: Path, source: str) -> Path:
    script = tmp_path / "opencode"
    script.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
if sys.argv[1:3] == ["debug", "config"]:
    config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
    if os.environ.get("HOSTILE_MANAGED_COMPACTION"):
        config["agent"]["compaction"]["model"] = "paid/compaction"
    print(json.dumps(config))
    sys.exit(0)
''' + source)
    script.chmod(0o755)
    return script


def test_keyless_opencode_selection_and_tool_round_trip(tmp_path, monkeypatch):
    """An explicit keyless provider/model prefix runs a full tool→result→text
    exchange while native tools stay disabled and all tokens are accounted."""
    script = executable(tmp_path, '''
import json, os, sys
cfg = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
assert cfg["permission"] == {"*": "deny"}
assert cfg["agent"]["hive-llm"]["permission"] == {"*": "deny"}
assert cfg["small_model"] == cfg["model"] == "opencode/test-free"
assert cfg["share"] == "disabled" and "--pure" in sys.argv
assert os.getcwd() != os.environ["CALLER_DIRECTORY"]
assert os.path.realpath(sys.argv[sys.argv.index("--dir") + 1]) == os.getcwd()
assert os.path.realpath(os.environ["PWD"]) == os.getcwd()
assert os.environ["OPENCODE_DISABLE_CLAUDE_CODE"] == "1"
request = json.load(sys.stdin)
assert request["messages"][0]["content"] == "Previous project context"
if request["messages"][-1]["role"] == "tool":
    assert request["messages"][-1]["content"] == "recorded hello"
    turn = {"text": "The note was recorded.", "tool_calls": []}
else:
    turn = {"text": "", "tool_calls": [{"name": "record", "arguments": {"text": "hello", "urgent": True}}]}
print(json.dumps({"type": "text", "part": {"text": json.dumps(turn)}}))
print(json.dumps({"type": "step_finish", "part": {"tokens": {"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 30, "write": 10}}}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CALLER_DIRECTORY", str(Path.cwd()))
    assert script.exists()
    assert candidate_providers(_config(orch_provider="opencode")) == ["opencode"]
    # opencode/ must be recognized before the generic OpenAI 'o' prefix.
    adapter = build_adapter(_config(orch_model="opencode/test-free"))
    recorded = []

    def record(text: str, urgent: bool = False) -> str:
        """Record a note."""
        recorded.append((text, urgent))
        return f"recorded {text}"

    result = ToolLoop(3).run(adapter, "Use the provided tools.",
                             [{"role": "user", "text": "Previous project context"}],
                             "Record hello urgently", ToolSet([record]))
    assert result.text == "The note was recorded." and result.rounds == 2
    assert recorded == [("hello", True)]
    assert result.usage == Usage(input_tokens=280, output_tokens=50)


def test_empty_toolset_preserves_callers_json_text(tmp_path, monkeypatch):
    """Todo triage uses a text transport; its requested JSON must reach the
    caller verbatim, without requiring or stripping a Hive tool envelope."""
    executable(tmp_path, '''
import json, os, sys
config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
assert config["model"] == config["small_model"] == "opencode/muse-spark-1.3-contributor-free"
assert "Response schema" not in config["agent"]["hive-llm"]["prompt"]
json.load(sys.stdin)
print(json.dumps({"type": "text", "part": {"text": "Checking the requested decisions."}}))
print(json.dumps({"type": "text", "part": {"text": '{"decisions": []}'}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    adapter = build_adapter(_config(orch_provider="opencode"))
    result = ToolLoop(1).run(adapter, "Answer with JSON only.", [], "Return empty decisions", ToolSet([]))
    assert json.loads(result.text) == {"decisions": []}


def test_completed_commentary_does_not_corrupt_the_final_tool_response(tmp_path, monkeypatch):
    """OpenCode emits complete text parts, not deltas: commentary precedes the
    final envelope, whose tools execute once and whose terminal text survives."""
    executable(tmp_path, '''
import json, sys
request = json.load(sys.stdin)
if request["messages"][-1]["role"] == "tool":
    turn = {"text": "Recorded once.", "tool_calls": []}
else:
    turn = {"text": "", "tool_calls": [{"name": "record", "arguments": {"text": "final"}}]}
for index, text in enumerate(["I will prepare the requested change.", json.dumps(turn)]):
    print(json.dumps({"type": "text", "part": {
        "id": str(index), "messageID": "assistant-message", "type": "text",
        "time": {"start": 1, "end": index + 2}, "text": text}}))
print(json.dumps({"type": "step_finish", "part": {"tokens": {"input": 3, "output": 2}}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    recorded = []

    def record(text: str) -> str:
        recorded.append(text)
        return "recorded"

    result = ToolLoop(2).run(build_adapter(_config(orch_provider="opencode")),
                             "", [], "Record a note", ToolSet([record]))
    assert recorded == ["final"]
    assert result.text == "Recorded once."
    assert result.usage == Usage(input_tokens=6, output_tokens=4)


def test_invalid_final_response_cannot_execute_an_earlier_valid_envelope(tmp_path, monkeypatch):
    """Selection is temporal, not a search for parseable JSON: a malformed
    final response fails without executing a superseded tool proposal."""
    from pydantic import ValidationError

    executable(tmp_path, '''
import json
turn = {"text": "", "tool_calls": [{"name": "record", "arguments": {"text": "superseded"}}]}
for text in [json.dumps(turn), "The final response is not a valid envelope."]:
    print(json.dumps({"type": "text", "part": {"text": text}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    recorded = []

    def record(text: str) -> str:
        recorded.append(text)
        return "recorded"

    with pytest.raises(ValidationError):
        ToolLoop(2).run(build_adapter(_config(orch_provider="opencode")),
                        "", [], "Record a note", ToolSet([record]))
    assert recorded == []


def test_managed_paid_auxiliary_model_blocks_planning_before_any_model_call(tmp_path, monkeypatch):
    """Later org/managed settings cannot bypass a free planner's model configuration."""
    marker = tmp_path / "model-called"
    executable(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HOSTILE_MANAGED_COMPACTION", "1")
    with pytest.raises(ValueError, match="OpenCode isolation preflight failed:.*compaction"):
        ToolLoop(1).run(build_adapter(_config(orch_provider="opencode")), "", [], "go", ToolSet([]))
    assert not marker.exists()


@pytest.mark.parametrize("invalid", [
    {"name": "record", "arguments": {"text": "second", "urgent": "yes"}},
    {"name": "record", "arguments": {"text": "second", "extra": "surprise"}},
    {"name": "unknown", "arguments": {}},
])
def test_invalid_later_call_prevents_all_side_effects(tmp_path, monkeypatch, invalid):
    """Even an initially valid request cannot execute when another request
    in the same model turn fails validation; no silent provider fallback."""
    from pydantic import ValidationError

    executable(tmp_path, '''
import json, os
print(json.dumps({"type": "text", "part": {"text": os.environ["SCRIPTED_TURN"]}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SCRIPTED_TURN", json.dumps({"text": "", "tool_calls": [
        {"name": "record", "arguments": {"text": "first"}}, invalid,
    ]}))
    recorded = []

    def record(text: str, urgent: bool = False) -> str:
        recorded.append(text)
        return "done"

    with pytest.raises(ValidationError):
        ToolLoop(2).run(build_adapter(_config(orch_provider="opencode")), "", [], "go", ToolSet([record]))
    assert recorded == []


def test_provider_errors_and_native_tool_attempts_fail_explicitly(tmp_path, monkeypatch):
    from hive.llm import ProviderUnavailable

    executable(tmp_path, '''
import os, sys
print(os.environ["SCRIPTED_EVENT"])
sys.exit(1)
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    adapter = build_adapter(_config(orch_provider="opencode"))
    for event, exception in [
        ({"type": "error", "error": {"data": {"statusCode": 429, "message": "quota reached"}}}, ProviderUnavailable),
        ({"type": "error", "error": {"data": {"statusCode": 400, "message": "bad request"}}}, RuntimeError),
        ({"type": "tool_use", "part": {"tool": "bash"}}, RuntimeError),
    ]:
        monkeypatch.setenv("SCRIPTED_EVENT", json.dumps(event))
        with pytest.raises(exception) as caught:
            ToolLoop(1).run(adapter, "", [], "go", ToolSet([]))
        assert type(caught.value) is exception


def test_timeout_kills_the_process_group(tmp_path, monkeypatch):
    """A stalled CLI and its child are both terminated at the timeout, so a
    failed planner call leaves no running process or native tool behind."""
    from hive.llm import ProviderUnavailable
    from hive.llm._opencode import OpenCodeAdapter

    pids = tmp_path / "pids.json"
    executable(tmp_path, '''
import json, os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(os.environ["PID_RECORD"], "w") as stream:
    json.dump([os.getpid(), child.pid], stream)
time.sleep(60)
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PID_RECORD", str(pids))
    with pytest.raises(ProviderUnavailable, match="timeout"):
        ToolLoop(1).run(OpenCodeAdapter(timeout_s=0.3), "", [], "go", ToolSet([]))
    for pid in json.loads(pids.read_text()):
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], text=True, capture_output=True).stdout.strip()
        assert not state or state.startswith("Z"), f"process {pid} survived timeout: {state}"


def test_opencode_orchestrator_proposes_a_durable_unapproved_plan(tmp_path, monkeypatch):
    """The real planner consumes the CLI adapter's validated requests, records
    usage/history, and creates only a draft; model output cannot start work."""
    from hive._control.orchestrator import Orchestrator
    from hive.models import OrchestratorRun, Plan, PlanItem, Project, Task
    from hive.persistence.blobstore import LocalBlobStore
    from hive.persistence.store import MemoryStore

    executable(tmp_path, '''
import json, sys
request = json.load(sys.stdin)
if request["messages"][-1]["role"] == "tool":
    assert "awaiting the human" in request["messages"][-1]["content"]
    turn = {"text": "Draft ready for review.", "tool_calls": []}
else:
    turn = {"text": "", "tool_calls": [{"name": "propose_plan", "arguments": {
        "goal": "Production simulation", "items_json": json.dumps([{"title": "Model the line"}])}}]}
print(json.dumps({"type": "text", "part": {"text": json.dumps(turn)}}))
print(json.dumps({"type": "step_finish", "part": {"tokens": {"input": 100, "output": 20}}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    class Spec:
        def __init__(self, *args):
            pass

        def sync(self):
            pass

        def digest(self):
            return "Build a production simulator."

    monkeypatch.setattr("hive._control.orchestrator.SpecRepo", Spec)
    store = MemoryStore()
    blobs = LocalBlobStore(tmp_path / "blobs")
    project = store.put(Project(name="simulator", spec_repo="https://github.com/o/sim.git"))
    orchestrator = Orchestrator(store, blobs, _config(orch_provider="opencode", data_dir=tmp_path))
    orchestrator.invoke(project.id, ["Draft the first iteration"])
    assert store.list(Plan, project_id=project.id)[0].status == "draft"
    assert store.list(PlanItem, project_id=project.id)[0].title == "Model the line"
    assert store.list(Task, project_id=project.id) == []
    run = store.list(OrchestratorRun, project_id=project.id)[0]
    assert run.input_tokens == 200 and run.output_tokens == 40
    assert run.model == "opencode/muse-spark-1.3-contributor-free"


def test_live_smoke_exercises_actual_snapshot_and_local_git(tmp_path, monkeypatch):
    """Keep the opt-in live scenario executable offline: the real caller
    supplies project state, commits its goal to isolated Git, and drafts work."""
    executable(tmp_path, '''
import json, sys
request = json.load(sys.stdin)
initial = request["messages"][0]["content"]
assert "STATE SNAPSHOT:\\nPROJECT smoke-arithmetic" in initial
assert "SPEC:" in initial and "A tiny deterministic Python arithmetic package" in initial
assert "Call propose_plan once" not in initial and "Do not ask questions" not in initial
if request["messages"][-1]["role"] == "tool":
    assert "committed " in request["messages"][-2]["content"]
    assert "awaiting the human" in request["messages"][-1]["content"]
    turn = {"text": "Draft ready for review.", "tool_calls": []}
else:
    turn = {"text": "", "tool_calls": [
        {"name": "commit_to_spec", "arguments": {
            "files_json": json.dumps({"iteration.md": "# Iteration\\nShip arithmetic package.\\n"}),
            "message": "Set arithmetic iteration"}},
        {"name": "propose_plan", "arguments": {
            "goal": "Ship arithmetic package", "items_json": json.dumps([{"title": "Implement arithmetic"}])}}
    ]}
print(json.dumps({"type": "text", "part": {"text": json.dumps(turn)}}))
''')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = subprocess.run([sys.executable, "scripts/smoke_opencode_planner.py"],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["included_only"] and evidence["daily_budget_usd"] == 0
    assert evidence["plans"][0]["status"] == "draft"
    assert evidence["spec_commits"] == ["Set arithmetic iteration", "Initial arithmetic mission"]
    assert evidence["execution_tasks"] == evidence["cost_usd"] == 0
