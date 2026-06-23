"""Structured result contracts for runner-executed coding agents.

The worker agent reports what happened; Hive's chief still owns the
state transition.  A task kind can opt into a Pydantic result model, which the
runner asks the agent to write to `.hive/result.json`.  The same warm session is
given validation errors and a chance to repair the JSON before the runner falls
back to legacy text markers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from hive.models import (
    TaskKind,
    TestReproOutcome,
    TestSweepOutcome,
    TestUxOutcome,
    Verdict,
)

RESULT_PATH = ".hive/result.json"


class AgentResultBase(BaseModel):
    task_id: str
    summary: str = ""


class VerifyResult(AgentResultBase):
    outcome: Literal["accept", "reject"]
    acceptance_checked: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    residual_risk: str = ""


class ResolveResult(AgentResultBase):
    outcome: Literal["fixed", "blocked"]
    tests_run: list[str] = Field(default_factory=list)
    branch_pushed: bool = False
    github_comment_posted: bool = False


class ReviewResult(AgentResultBase):
    outcome: Literal["accept", "reject"]
    tests_run: list[str] = Field(default_factory=list)
    changes_pushed: bool = False
    github_comment_posted: bool = False


class TestRefreshResult(AgentResultBase):
    outcome: Literal["done"]
    active_story_count: int = Field(ge=0)
    stories_changed: list[str]
    created_story_keys: list[str]
    updated_story_keys: list[str]
    archived_story_keys: list[str]
    changed_files: list[str]
    commit_sha: str
    questions: list[str]


class SweepFindingResult(BaseModel):
    kind: Literal["bug", "ux_smell"]
    severity: str
    summary: str
    detail: str
    oracle: str
    evidence_blobs: list[str] = Field(default_factory=list)


class TestSweepResult(AgentResultBase):
    outcome: Literal["pass", "findings", "blocked"]
    fidelity: Literal["local", "docker"] = "local"
    findings: list[SweepFindingResult] = Field(default_factory=list)


class TestReproduceResult(AgentResultBase):
    outcome: Literal["confirmed", "not_reproduced"]
    evidence_blobs: list[str] = Field(default_factory=list)


class TestJudgeResult(AgentResultBase):
    outcome: Literal["improvable", "constrained", "disagree"]
    evidence_blobs: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ResultSpec:
    model: type[BaseModel]
    path: str = RESULT_PATH
    repair_attempts: int = 2

    def instructions(self, task_id: str) -> str:
        schema = json.dumps(self.model.model_json_schema(), indent=2, sort_keys=True)
        return f"""

Hive structured result contract:
- Before your final response, write a JSON file at `{self.path}`.
- The JSON must validate against the schema below.
- Use this exact task_id: `{task_id}`.
- Use lowercase enum values exactly as shown in the schema.
- Keep your normal final report concise; Hive will read `{self.path}` as the authoritative structured report.

Schema:
```json
{schema}
```
""".rstrip()


ResultSpecLike = ResultSpec | type[BaseModel] | None


@dataclass
class AgentCallResult:
    text: str
    is_error: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    structured_result: dict = field(default_factory=dict)
    structured_result_error: str = ""
    attempts: int = 1
    raw_result: object | None = None


RESULT_SPECS: dict[TaskKind, ResultSpec] = {
    TaskKind.verify: ResultSpec(VerifyResult),
    TaskKind.resolve: ResultSpec(ResolveResult),
    TaskKind.review: ResultSpec(ReviewResult),
    TaskKind.test_refresh: ResultSpec(TestRefreshResult),
    TaskKind.test_sweep: ResultSpec(TestSweepResult),
    TaskKind.test_reproduce: ResultSpec(TestReproduceResult),
    TaskKind.test_judge: ResultSpec(TestJudgeResult),
}


def result_spec_for_task(kind: str | TaskKind) -> ResultSpec | None:
    try:
        task_kind = TaskKind(kind)
    except ValueError:
        return None
    return RESULT_SPECS.get(task_kind)


def as_result_spec(spec: ResultSpecLike) -> ResultSpec | None:
    if spec is None or isinstance(spec, ResultSpec):
        return spec
    if isinstance(spec, type) and issubclass(spec, BaseModel):
        return ResultSpec(spec)
    raise TypeError("result spec must be a ResultSpec, Pydantic model type, or None")


def verdict_from_structured(kind: str | TaskKind, payload: dict) -> Verdict:
    if not payload:
        return Verdict.none
    outcome = str(payload.get("outcome") or "").lower()
    try:
        task_kind = TaskKind(kind)
    except ValueError:
        return Verdict.none
    if task_kind in (TaskKind.verify, TaskKind.review):
        if outcome == "accept":
            return Verdict.accept
        if outcome == "reject":
            return Verdict.reject
    if task_kind == TaskKind.resolve:
        if outcome == "fixed":
            return Verdict.accept
        if outcome == "blocked":
            return Verdict.reject
    if task_kind == TaskKind.test_refresh:
        return Verdict.accept if outcome == "done" else Verdict.none
    if task_kind == TaskKind.test_sweep:
        if outcome == "pass":
            return Verdict.accept
        if outcome in {"findings", "blocked"}:
            return Verdict.reject
    if task_kind == TaskKind.test_reproduce:
        if outcome == "confirmed":
            return Verdict.accept
        if outcome == "not_reproduced":
            return Verdict.reject
    if task_kind == TaskKind.test_judge:
        if outcome == "improvable":
            return Verdict.accept
        if outcome in {"constrained", "disagree"}:
            return Verdict.reject
    return Verdict.none


def test_sweep_outcome(payload: dict) -> TestSweepOutcome:
    outcome = str(payload.get("outcome") or "").lower()
    if outcome == "pass":
        return TestSweepOutcome.passed
    if outcome == "findings":
        return TestSweepOutcome.findings
    if outcome == "blocked":
        return TestSweepOutcome.blocked
    return TestSweepOutcome.none


def test_repro_outcome(payload: dict) -> TestReproOutcome:
    outcome = str(payload.get("outcome") or "").lower()
    if outcome == "confirmed":
        return TestReproOutcome.confirmed
    if outcome == "not_reproduced":
        return TestReproOutcome.not_reproduced
    return TestReproOutcome.none


def test_ux_outcome(payload: dict) -> TestUxOutcome:
    outcome = str(payload.get("outcome") or "").lower()
    if outcome == "improvable":
        return TestUxOutcome.improvable
    if outcome == "constrained":
        return TestUxOutcome.constrained
    if outcome == "disagree":
        return TestUxOutcome.disagree
    return TestUxOutcome.none


def call_agent(
    agent,
    task: dict,
    project_dir: Path,
    result_spec: ResultSpecLike,
) -> AgentCallResult:
    """Run an agent task and, when a spec is supplied, validate `.hive/result.json`.

    On missing or invalid JSON, the same warm agent session receives the validation
    error and can repair only the result file.  If repair still fails, callers get
    the agent text and a `structured_result_error` so legacy marker parsing can
    still decide the task.
    """
    spec = as_result_spec(result_spec)
    task_id = str(task.get("id") or "")
    agent_name = str(task.get("kind") or "agent")
    instructions = str(task.get("instructions") or "")
    if spec:
        _prepare_result_path(project_dir, spec)
        instructions = f"{instructions}\n\n{spec.instructions(task_id)}"

    result = agent.run(instructions, project_dir, agent_name=agent_name)
    total = _from_agent_result(result)
    if total.is_error or not spec:
        return total

    payload, error = _read_result(project_dir, spec, task_id)
    attempts = 1
    while error and attempts <= spec.repair_attempts:
        attempts += 1
        repair = agent.run(
            _repair_prompt(spec, task_id, error),
            project_dir,
            agent_name=f"{agent_name}-result-repair",
        )
        repair_total = _from_agent_result(repair)
        total = _merge(total, repair_total)
        if repair_total.is_error:
            error = f"repair agent errored: {repair_total.text}"
            break
        payload, error = _read_result(project_dir, spec, task_id)

    total.structured_result = payload
    total.structured_result_error = error
    total.attempts = attempts
    return total


call_agent_with_result = call_agent


def _prepare_result_path(project_dir: Path, spec: ResultSpec) -> None:
    path = project_dir / spec.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    exclude = project_dir / ".git" / "info" / "exclude"
    if exclude.exists():
        text = exclude.read_text()
        if ".hive/" not in text:
            exclude.write_text(text.rstrip() + "\n.hive/\n")


def _read_result(project_dir: Path, spec: ResultSpec, task_id: str) -> tuple[dict, str]:
    path = project_dir / spec.path
    if not path.exists():
        return {}, f"{spec.path} was not created"
    try:
        parsed = spec.model.model_validate_json(path.read_text())
    except (OSError, ValidationError, ValueError) as exc:
        return {}, str(exc)
    payload = parsed.model_dump(mode="json")
    if payload.get("task_id") != task_id:
        return {}, f"{spec.path} task_id {payload.get('task_id')!r} did not match {task_id!r}"
    return payload, ""


def _repair_prompt(spec: ResultSpec, task_id: str, error: str) -> str:
    schema = json.dumps(spec.model.model_json_schema(), indent=2, sort_keys=True)
    return f"""Hive could not validate `{spec.path}` for task `{task_id}`.

Validation error:
```
{error}
```

Do not change product code, tests, commits, branches, or artifacts. Only create or repair `{spec.path}` so it validates against this schema and uses task_id `{task_id}`:

```json
{schema}
```

Reply briefly after fixing the file."""


def _from_agent_result(result) -> AgentCallResult:
    query = getattr(result, "query", result)
    return AgentCallResult(
        text=getattr(result, "text", ""),
        is_error=bool(getattr(result, "is_error", False)),
        cost_usd=getattr(query, "cost_usd", 0.0) or 0.0,
        input_tokens=getattr(query, "input_tokens", 0) or 0,
        output_tokens=getattr(query, "output_tokens", 0) or 0,
        raw_result=result,
    )


def _merge(left: AgentCallResult, right: AgentCallResult) -> AgentCallResult:
    return AgentCallResult(
        text=right.text or left.text,
        is_error=left.is_error or right.is_error,
        cost_usd=left.cost_usd + right.cost_usd,
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        structured_result=right.structured_result or left.structured_result,
        structured_result_error=right.structured_result_error or left.structured_result_error,
        attempts=max(left.attempts, right.attempts),
        raw_result=right.raw_result or left.raw_result,
    )
