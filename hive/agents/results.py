"""Structured results from coding-agent runs.

An agent's final chat message is prose; automation wants a verdict it can
switch on. A `ResultSpec` wraps any Pydantic model: the agent is asked to write
a validating JSON file at `.hive/result.json`, and the same warm session gets
the validation errors and a bounded chance to repair the file before callers
fall back to parsing the text. `AgentCallResult` carries both the prose and the
validated payload plus cost/token accounting summed across repair rounds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

RESULT_PATH = ".hive/result.json"


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
    session_handle: str = ""  # provider session id, when the backend exposes one
    raw_result: object | None = None


def as_result_spec(spec: ResultSpecLike) -> ResultSpec | None:
    if spec is None or isinstance(spec, ResultSpec):
        return spec
    if isinstance(spec, type) and issubclass(spec, BaseModel):
        return ResultSpec(spec)
    raise TypeError("result spec must be a ResultSpec, Pydantic model type, or None")


def call_agent(
    agent,
    *,
    instructions: str,
    workdir: Path,
    result_spec: ResultSpecLike = None,
    task_id: str = "",
    agent_name: str = "agent",
) -> AgentCallResult:
    """Run an agent task and, when a spec is supplied, validate `.hive/result.json`.

    On missing or invalid JSON, the same warm agent session receives the validation
    error and can repair only the result file.  If repair still fails, callers get
    the agent text and a `structured_result_error` so text-level parsing can
    still decide the task.
    """
    spec = as_result_spec(result_spec)
    if spec:
        _prepare_result_path(workdir, spec)
        instructions = f"{instructions}\n\n{spec.instructions(task_id)}"

    result = agent.run(instructions, workdir, agent_name=agent_name)
    total = _from_agent_result(result)
    if total.is_error or not spec:
        return total

    payload, error = _read_result(workdir, spec, task_id)
    attempts = 1
    while error and attempts <= spec.repair_attempts:
        attempts += 1
        repair = agent.run(
            _repair_prompt(spec, task_id, error),
            workdir,
            agent_name=f"{agent_name}-result-repair",
        )
        repair_total = _from_agent_result(repair)
        total = _merge(total, repair_total)
        if repair_total.is_error:
            error = f"repair agent errored: {repair_total.text}"
            break
        payload, error = _read_result(workdir, spec, task_id)

    total.structured_result = payload
    total.structured_result_error = error
    total.attempts = attempts
    return total


def _prepare_result_path(workdir: Path, spec: ResultSpec) -> None:
    path = workdir / spec.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    exclude = workdir / ".git" / "info" / "exclude"
    if exclude.exists():
        text = exclude.read_text()
        if ".hive/" not in text:
            exclude.write_text(text.rstrip() + "\n.hive/\n")


def _read_result(workdir: Path, spec: ResultSpec, task_id: str) -> tuple[dict, str]:
    path = workdir / spec.path
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
        session_handle=right.session_handle or left.session_handle,
        raw_result=right.raw_result or left.raw_result,
    )
