"""Provider-agnostic tool-using LLM loop.

An `LLMAdapter` is a stateful conversation with one provider: `start` seeds it,
`step` returns the next model turn (final text, or tool calls to run), and
`add_tool_results` feeds executed results back. `ToolLoop` drives any adapter to
a final answer, dispatching tool calls through a `ToolSet`. Providers differ
only in the adapter; the loop, tool schemas, and dispatch are shared — adding a
provider is one small adapter, and the same machinery powers any future agent
role (verify, maintain) beyond the orchestrator.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

log = logging.getLogger("hive.llm")


@dataclass
class ToolCall:
    name: str
    arguments: dict | str  # Gemini hands back a dict, OpenAI a JSON string
    id: str = ""  # provider call id, echoed back with the result


@dataclass
class ToolResult:
    call: ToolCall
    content: str


@dataclass
class Completion:
    """One model turn: tool calls to execute, or (when empty) final text."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


@runtime_checkable
class LLMAdapter(Protocol):
    """A stateful single-provider conversation driven by `ToolLoop`."""

    def start(self, system: str, history: list[dict], user_msg: str, toolset: ToolSet) -> None: ...

    def step(self) -> Completion: ...

    def add_tool_results(self, results: list[ToolResult]) -> None: ...


def _json_type(annotation) -> str:
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    return "string"


class ToolSet:
    """A set of tool callables. Generates provider schemas from their signatures
    (the docstring is the model-facing description) and dispatches calls back to
    them. Callables must have real (non-stringized) annotations — schema
    inference reads them at runtime."""

    def __init__(self, functions: list[Callable]) -> None:
        self._fns: dict[str, Callable] = {fn.__name__: fn for fn in functions}

    def callables(self) -> list[Callable]:
        return list(self._fns.values())

    def openai_schemas(self) -> list[dict]:
        return [self._openai_schema(fn) for fn in self._fns.values()]

    @staticmethod
    def _openai_schema(fn) -> dict:
        properties: dict[str, dict] = {}
        required: list[str] = []
        for name, param in inspect.signature(fn).parameters.items():
            if name == "self":
                continue
            properties[name] = {"type": _json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(name)
        return {
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": inspect.getdoc(fn) or "",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    def dispatch(self, call: ToolCall) -> ToolResult:
        fn = self._fns.get(call.name)
        if fn is None:
            return ToolResult(call, f"error: unknown tool {call.name!r}")
        args = call.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError as exc:
                return ToolResult(call, f"error: invalid JSON arguments for {call.name}: {exc}")
        try:
            return ToolResult(call, str(fn(**args)))
        except Exception as exc:
            log.exception("tool %s failed", call.name)
            return ToolResult(call, f"error: tool {call.name} raised {type(exc).__name__}: {exc}")


class ToolLoop:
    """Drives an adapter: step → run any tool calls → feed results → repeat,
    until the model returns final text or the round budget is exhausted."""

    def __init__(self, max_rounds: int) -> None:
        self.max_rounds = max_rounds

    def run(
        self,
        adapter: LLMAdapter,
        system: str,
        history: list[dict],
        user_msg: str,
        toolset: ToolSet,
    ) -> str:
        adapter.start(system, history, user_msg, toolset)
        for _ in range(self.max_rounds):
            completion = adapter.step()
            if completion.is_final:
                return completion.text or "(no text)"
            adapter.add_tool_results([toolset.dispatch(c) for c in completion.tool_calls])
        return "Stopped after maximum orchestrator tool-call rounds."
