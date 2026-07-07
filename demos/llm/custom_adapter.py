"""Demo: bring your own model to the tool loop — `hive.llm` standalone.

Task: you have a model `hive.llm` has never heard of (a local llama server, a
lab prototype, a rules engine) and want hive's whole tool ecosystem to work
with it. The seam is the three-method `LLMAdapter` protocol: implement
`start`/`step`/`add_tool_results` and every `ToolLoop` caller runs unchanged.

    uv run python demos/llm/custom_adapter.py

Offline: the "model" here is a ~30-line keyword matcher — enough brains to
route a question to the right tool and phrase the answer.
"""

import json

from hive.llm import Completion, LLMAdapter, ToolCall, ToolLoop, ToolSet, Usage, extract_json


def roll_dice(sides: int, count: int) -> str:
    """Roll `count` dice with `sides` sides (deterministic demo dice)."""
    rolls = [(i * 7) % sides + 1 for i in range(1, count + 1)]
    return json.dumps({"rolls": rolls, "total": sum(rolls)})


def look_up_capital(country: str) -> str:
    """The capital city of a country."""
    return {"france": "Paris", "japan": "Tokyo"}.get(country.lower(), "unknown")


class TinyRuleModel:
    """A hand-rolled 'model' that satisfies the LLMAdapter protocol."""

    def start(self, system: str, history: list, user_msg: str, toolset: ToolSet) -> None:
        self.question = user_msg.lower()
        self.pending: ToolCall | None = None
        self.answer = ""

    def step(self) -> Completion:
        if self.answer:
            return Completion(text=self.answer, usage=Usage(3, 9))
        if "dice" in self.question:
            self.pending = ToolCall("roll_dice", {"sides": 6, "count": 3})
        elif "capital" in self.question:
            country = self.question.rstrip("?").split()[-1]
            self.pending = ToolCall("look_up_capital", {"country": country})
        else:
            return Completion(text="I only know dice and capitals.", usage=Usage(3, 3))
        return Completion(tool_calls=[self.pending], usage=Usage(5, 2))

    def add_tool_results(self, results) -> None:
        content = results[0].content
        if self.pending.name == "roll_dice":
            total = extract_json(content)["total"]  # the shared JSON parser
            self.answer = f"You rolled a total of {total}."
        else:
            self.answer = f"The capital is {content}."


assert isinstance(TinyRuleModel(), LLMAdapter)  # the protocol is runtime-checkable

loop = ToolLoop(max_rounds=4)
tools = ToolSet([roll_dice, look_up_capital])
for question in ("Roll some dice for me", "What is the capital of France?"):
    result = loop.run(TinyRuleModel(), system="", history=[], user_msg=question, toolset=tools)
    print(f"Q: {question}\nA: {result.text}  ({result.rounds} rounds)\n")

schema = tools.openai_schemas()[0]["function"]
print(f"schema inferred from roll_dice's signature: {json.dumps(schema['parameters'])}")
