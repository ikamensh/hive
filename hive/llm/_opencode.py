"""Keyless LLM access through the installed OpenCode CLI.

Each round is an isolated, non-coding session. OpenCode returns text or proposed
Hive tool calls; this adapter validates the whole turn before ToolLoop executes
anything. No resident server, native tools, or project checkout is involved.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from typing import Literal, Union

from pydantic import ConfigDict, create_model

from hive.llm._core import Completion, ProviderUnavailable, ToolCall, ToolResult, ToolSet, Usage

DEFAULT_OPENCODE_MODEL = "opencode/muse-spark-1.3-contributor-free"


def _turn_model(toolset: ToolSet):
    variants = []
    for fn in toolset.callables():
        fields = {
            name: (param.annotation if param.annotation is not inspect.Parameter.empty else str,
                   param.default if param.default is not inspect.Parameter.empty else ...)
            for name, param in inspect.signature(fn).parameters.items()
        }
        arguments = create_model(f"{fn.__name__}_arguments", __config__=ConfigDict(extra="forbid", strict=True), **fields)
        variants.append(create_model(
            f"{fn.__name__}_request", __config__=ConfigDict(extra="forbid", strict=True),
            __doc__=inspect.getdoc(fn), name=(Literal[fn.__name__], ...), arguments=(arguments, ...),
        ))
    if not variants:
        return None
    return create_model("HiveTurn", __config__=ConfigDict(extra="forbid", strict=True),
                        text=(str, ...), tool_calls=(list[Union[tuple(variants)]], ...))


def _stop_process(process: subprocess.Popen) -> None:
    # Descendants can keep stdout open even after the immediate child exits.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.communicate()


class OpenCodeAdapter:
    def __init__(self, model: str = "", *, timeout_s: float = 120.0):
        self.model = model.strip() or DEFAULT_OPENCODE_MODEL
        if "/" not in self.model or not all(self.model.split("/", 1)):
            raise ValueError("OpenCode models must use provider/model format")
        self.timeout_s = timeout_s
        self.messages: list[dict] = []
        self.system = ""
        self.turn_model = None
        self.round = 0

    def start(self, system: str, history: list[dict], user_msg: str, toolset: ToolSet) -> None:
        self.system = system
        self.turn_model = _turn_model(toolset)
        self.round = 0
        self.messages = [{"role": "assistant" if item["role"] == "model" else item["role"],
                          "content": item["text"]} for item in history]
        self.messages.append({"role": "user", "content": user_msg})

    def step(self) -> Completion:
        system = self.system
        if self.turn_model is not None:
            system += (
                "\nReturn only JSON matching the response schema. Request Hive tools in tool_calls; "
                "Hive executes them and returns their results. Use an empty tool_calls list and final "
                "text when finished.\nResponse schema:\n" + json.dumps(self.turn_model.model_json_schema())
            )
        stdout, stderr, returncode = self._run(system, json.dumps({"messages": self.messages}))
        text, usage = "", Usage()
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            kind, part = event["type"], event.get("part", {})
            if kind == "error":
                error = event.get("error", {})
                data = error.get("data", error)
                message = str(data.get("message") or error.get("name") or "OpenCode provider failed")
                status = data.get("statusCode")
                if status is not None and status not in (401, 403, 404, 429) and status < 500:
                    raise RuntimeError(message)
                raise ProviderUnavailable(message)
            if kind == "tool_use":
                raise RuntimeError("OpenCode attempted a native tool; Hive LLM sessions only return tool requests")
            if kind == "text":
                # CLI text events are completed parts, not streaming deltas.
                # The final part is the response; earlier parts may be commentary.
                text = part["text"]
            if kind == "step_finish":
                tokens = part.get("tokens", {})
                cache = tokens.get("cache", {})
                usage += Usage(tokens.get("input", 0) + cache.get("read", 0) + cache.get("write", 0),
                               tokens.get("output", 0) + tokens.get("reasoning", 0))
        if returncode:
            raise RuntimeError(f"OpenCode exited {returncode}: {stderr[-1000:]}")
        if not text.strip():
            raise RuntimeError("OpenCode returned no model text")
        self.round += 1
        if self.turn_model is None:
            self.messages.append({"role": "assistant", "content": text})
            return Completion(text=text, usage=usage)
        # Strictly validate every name, argument type, and extra field before
        # returning even the first tool request to Hive's side-effect boundary.
        turn = self.turn_model.model_validate_json(text, strict=True)
        self.messages.append({"role": "assistant", "content": turn.model_dump()})
        return Completion(text=turn.text, usage=usage, tool_calls=[
            ToolCall(name=call.name, arguments=call.arguments.model_dump(), id=f"{self.round}-{index}")
            for index, call in enumerate(turn.tool_calls)
        ])

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.messages.extend({"role": "tool", "name": result.call.name,
                              "tool_call_id": result.call.id, "content": result.content} for result in results)

    def _run(self, system: str, prompt: str) -> tuple[str, str, int]:
        from kodo.opencode_config import isolated_opencode_config

        with tempfile.TemporaryDirectory(prefix="hive-llm-opencode-") as directory, \
             isolated_opencode_config(self.model, Path(directory), agent="hive-llm",
                                      prompt=system, permission="deny",
                                      repository_instructions=False) as env:
            try:
                process = subprocess.Popen(
                    ["opencode", "run", "--pure", "--format", "json", "--model", self.model,
                     "--agent", "hive-llm", "--title", "Hive LLM", "--dir", directory],
                    cwd=directory, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True,
                )
            except FileNotFoundError as exc:
                raise ProviderUnavailable("OpenCode CLI is not installed or not on PATH") from exc
            try:
                stdout, stderr = process.communicate(prompt, timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                _stop_process(process)
                raise ProviderUnavailable(f"OpenCode exceeded its {self.timeout_s:g}s timeout") from exc
            except BaseException:
                _stop_process(process)
                raise
            return stdout, stderr, process.returncode
