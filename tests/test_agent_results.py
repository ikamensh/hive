import json

from hive.agent_results import ResultSpec, VerifyResult, call_agent


class FakeResult:
    def __init__(self, text, *, cost=0.0, input_tokens=0, output_tokens=0, is_error=False):
        self.text = text
        self.is_error = is_error
        self.query = self
        self.cost_usd = cost
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeAgent:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def run(self, instructions, project_dir, agent_name):
        self.calls.append((agent_name, instructions))
        step = self.steps.pop(0)
        return step(project_dir, instructions)


def _write_result(project_dir, payload):
    path = project_dir / ".hive" / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_call_agent_accepts_pydantic_model_and_reads_valid_result(tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    exclude = project_dir / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("# local excludes\n")
    task = {"id": "task-1", "kind": "verify", "instructions": "check the work"}

    def write_valid(project_dir, instructions):
        assert "Hive structured result contract" in instructions
        assert "`task-1`" in instructions
        _write_result(
            project_dir,
            {
                "task_id": "task-1",
                "outcome": "accept",
                "acceptance_checked": ["smoke"],
                "commands_run": ["pytest"],
            },
        )
        return FakeResult("VERDICT omitted on purpose", cost=1.5, input_tokens=10, output_tokens=3)

    result = call_agent(FakeAgent([write_valid]), task, project_dir, VerifyResult)

    assert result.structured_result["outcome"] == "accept"
    assert result.structured_result["commands_run"] == ["pytest"]
    assert result.structured_result_error == ""
    assert result.cost_usd == 1.5
    assert result.input_tokens == 10
    assert result.output_tokens == 3
    assert result.attempts == 1
    assert ".hive/" in exclude.read_text()


def test_call_agent_repairs_missing_result_in_same_session(tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    task = {"id": "task-2", "kind": "verify", "instructions": "check the work"}

    def forget(project_dir, instructions):
        assert "check the work" in instructions
        return FakeResult("forgot result", cost=1.0, input_tokens=7, output_tokens=2)

    def repair(project_dir, instructions):
        assert ".hive/result.json was not created" in instructions
        _write_result(project_dir, {"task_id": "task-2", "outcome": "reject"})
        return FakeResult("fixed result", cost=0.25, input_tokens=4, output_tokens=1)

    agent = FakeAgent([forget, repair])
    result = call_agent(agent, task, project_dir, ResultSpec(VerifyResult, repair_attempts=1))

    assert [name for name, _ in agent.calls] == ["verify", "verify-result-repair"]
    assert result.text == "fixed result"
    assert result.structured_result["outcome"] == "reject"
    assert result.structured_result_error == ""
    assert result.cost_usd == 1.25
    assert result.input_tokens == 11
    assert result.output_tokens == 3
    assert result.attempts == 2


def test_call_agent_returns_validation_error_after_repair_budget(tmp_path):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    task = {"id": "task-3", "kind": "verify", "instructions": "check the work"}

    def wrong_task(project_dir, instructions):
        _write_result(project_dir, {"task_id": "other", "outcome": "accept"})
        return FakeResult("wrong id")

    def still_wrong(project_dir, instructions):
        assert "did not match" in instructions
        _write_result(project_dir, {"task_id": "other", "outcome": "accept"})
        return FakeResult("still wrong")

    result = call_agent(
        FakeAgent([wrong_task, still_wrong]),
        task,
        project_dir,
        ResultSpec(VerifyResult, repair_attempts=1),
    )

    assert result.structured_result == {}
    assert "did not match" in result.structured_result_error
    assert result.attempts == 2
