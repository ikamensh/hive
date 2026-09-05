"""hive.agents.run — the one-call agent lifecycle, without real CLIs.

`run_agent` is what both hive's runner daemon and standalone scripts call, so
its contract is pinned here with a fake kodo Agent: instructions flow through,
the structured result round-trips, the session hook fires before the run, and
the provider session handle lands on the result.
"""

import json
import os
import sys
import pytest

import hive.agents.run as run_mod
from hive.agents import run_agent, session_handle


@pytest.mark.parametrize(("override", "requested", "expected"), [
    ("", "", "gpt-6-astra"),
    ("configured-model", "", "configured-model"),
    ("configured-model", "requested-model", "requested-model"),
])
def test_codex_probe_and_tasks_use_hives_selected_model(
    tmp_path, monkeypatch, override, requested, expected,
):
    """A probe cannot inherit Kodo's stale default and reject usable capacity."""
    cli = tmp_path / "codex"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, sys
print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":sys.argv[sys.argv.index("-m")+1]}}))
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HIVE_CODEX_MODEL", override)
    result = run_agent("codex", "probe", tmp_path, model=requested, timeout_s=10)
    assert not result.is_error
    assert result.text == expected


def test_opencode_runs_through_the_agent_lifecycle(tmp_path, monkeypatch):
    """OpenCode's JSON events, selected model, and resumed session cross the real adapter."""
    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, sys
from pathlib import Path
Path("arguments.json").write_text(json.dumps(sys.argv[1:]))
print(json.dumps({"type":"text", "sessionID":"session-test", "part":{"text":"done"}}))
print(json.dumps({"type":"step_finish", "part":{"tokens":{"input":3,"output":2}}}))
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    result = run_agent("opencode", "do it", tmp_path, model="opencode/test-free",
                       resume_session="session-test", timeout_s=10)
    args = json.loads((tmp_path / "arguments.json").read_text())
    assert args[:3] == ["run", "--format", "json"]
    assert args[args.index("--model")+1] == "opencode/test-free"
    assert args[args.index("--session")+1] == "session-test"
    assert result.text == "done" and not result.is_error
    assert result.session_handle == "session-test"


class FakeSession:
    def __init__(self):
        self.terminated = False

    def session_id(self):
        return "sess-42"

    def terminate(self):
        self.terminated = True


class FakeAgent:
    """Stands in for kodo.agent.Agent: context manager + run()."""

    last = None

    def __init__(self, session, max_turns, timeout_s):
        self.session = session
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        FakeAgent.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, instructions, workdir, agent_name):
        self.ran = (instructions, workdir, agent_name)
        (workdir / ".hive").mkdir(exist_ok=True)
        (workdir / ".hive" / "result.json").write_text(
            json.dumps({"task_id": "t1", "summary": "did it", "outcome": "accept"})
        )

        class R:
            text = "all done"
            is_error = False
            cost_usd = 0.5
            input_tokens = 3
            output_tokens = 2

        return R()


def test_run_agent_drives_session_result_and_handle(tmp_path, monkeypatch):
    from pydantic import BaseModel

    class Outcome(BaseModel):
        task_id: str
        summary: str
        outcome: str

    seen_sessions = []
    monkeypatch.setattr(run_mod, "make_session", lambda b, m, r: FakeSession())
    monkeypatch.setattr("kodo.agent.Agent", FakeAgent)

    result = run_agent(
        "claude",
        "do the thing",
        tmp_path,
        result_spec=Outcome,
        task_id="t1",
        max_turns=7,
        timeout_s=60.0,
        on_session=seen_sessions.append,
    )

    assert len(seen_sessions) == 1  # hook fired, before/independent of outcome
    assert FakeAgent.last.max_turns == 7 and FakeAgent.last.timeout_s == 60.0
    assert "do the thing" in FakeAgent.last.ran[0]
    assert FakeAgent.last.ran[2] == "claude"  # agent_name defaults to backend
    assert result.structured_result["outcome"] == "accept"
    assert result.structured_result_error == ""
    assert result.session_handle == "sess-42"
    assert result.cost_usd == 0.5


def test_session_handle_variants():
    """Callable, plain attribute, missing, and raising handles all normalize."""

    class Callable_:
        def session_id(self):
            return "abc"

    class Plain:
        session_id = "xyz"

    class Missing:
        pass

    class Raising:
        def session_id(self):
            raise RuntimeError("no id yet")

    assert session_handle(Callable_()) == "abc"
    assert session_handle(Plain()) == "xyz"
    assert session_handle(Missing()) == ""
    assert session_handle(Raising()) == ""
