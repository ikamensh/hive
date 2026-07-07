"""Demo: a tool-calling calculator agent — `hive.llm` standalone.

Task: give a model real tools (plain Python functions — signatures become the
schema, docstrings the description) and drive the call → execute → feed-back
cycle until it answers. `ToolLoop` owns that cycle for any provider.

    uv run python demos/llm/calculator_agent.py

With GEMINI_API_KEY or OPENAI_API_KEY set, a real model solves the word
problem; otherwise a scripted adapter replays the same conversation shape, so
the demo always runs and always exercises the same public API.
"""

import os
from types import SimpleNamespace

from hive.llm import Completion, LoopResult, ToolCall, ToolLoop, ToolSet, Usage, build_adapter

calls_made = []


def add(a: float, b: float) -> float:
    """Add two numbers."""
    calls_made.append(f"add({a}, {b})")
    return a + b


def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    calls_made.append(f"multiply({a}, {b})")
    return a * b


QUESTION = "A crate holds 12 boxes of 34 eggs plus 7 loose eggs. How many eggs in total? Use the tools."


class ScriptedAdapter:
    """A deterministic stand-in satisfying the LLMAdapter protocol."""

    def start(self, system, history, user_msg, toolset):
        self.turn = 0

    def step(self) -> Completion:
        self.turn += 1
        if self.turn == 1:
            return Completion(tool_calls=[ToolCall("multiply", {"a": 12, "b": 34})], usage=Usage(20, 5))
        if self.turn == 2:
            return Completion(tool_calls=[ToolCall("add", {"a": 408, "b": 7}, id="c2")], usage=Usage(25, 5))
        return Completion(text=f"The crate holds {self.results[-1].content} eggs.", usage=Usage(30, 10))

    def add_tool_results(self, results):
        self.results = results


def pick_adapter():
    config = SimpleNamespace(
        orch_provider="auto",
        orch_model=os.environ.get("HIVE_ORCH_MODEL", ""),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
    )
    try:
        return build_adapter(config), "a real model"
    except ValueError:
        return ScriptedAdapter(), "the scripted offline adapter (no API key found)"


adapter, who = pick_adapter()
print(f"asking {who}: {QUESTION}\n")

result: LoopResult = ToolLoop(max_rounds=8).run(
    adapter,
    system="You are a careful calculator. Use the tools for all arithmetic.",
    history=[],
    user_msg=QUESTION,
    toolset=ToolSet([add, multiply]),
)

print(f"tool calls the model made: {calls_made}")
print(f"answer after {result.rounds} rounds: {result.text}")
print(f"tokens: {result.usage.input_tokens} in / {result.usage.output_tokens} out")
assert "415" in result.text
