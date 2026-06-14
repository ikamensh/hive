"""The unified LLM layer: the shared tool loop + schema/dispatch, and each
adapter's translation, all without a network or real provider call."""

import pytest

from hive.config import Config
from hive.llm import Completion, ToolCall, ToolLoop, ToolSet, Usage, build_adapter
from hive.llm.gemini import GeminiAdapter


# -- ToolSet: schema generation + dispatch -----------------------------------

class _Surface:
    def __init__(self):
        self.calls = []

    def note(self, text: str, urgent: bool = False) -> str:
        """Record a note."""
        self.calls.append((text, urgent))
        return f"noted {text!r} urgent={urgent}"

    def boom(self) -> str:
        """Always fails."""
        raise RuntimeError("kaboom")


def test_toolset_schema_reads_signature_and_docstring():
    surface = _Surface()
    [schema] = [s for s in ToolSet([surface.note]).openai_schemas()]
    fn = schema["function"]
    assert schema["type"] == "function"
    assert fn["name"] == "note" and fn["description"] == "Record a note."
    params = fn["parameters"]
    assert params["properties"] == {"text": {"type": "string"}, "urgent": {"type": "boolean"}}
    assert params["required"] == ["text"]  # urgent has a default


def test_toolset_dispatch_parses_json_args_and_reports_errors():
    surface = _Surface()
    tools = ToolSet([surface.note, surface.boom])

    ok = tools.dispatch(ToolCall(name="note", arguments='{"text": "hi", "urgent": true}', id="1"))
    assert ok.content == "noted 'hi' urgent=True" and surface.calls == [("hi", True)]

    # dict args (Gemini-style) work too
    assert tools.dispatch(ToolCall(name="note", arguments={"text": "yo"})).content.startswith("noted 'yo'")

    assert "unknown tool" in tools.dispatch(ToolCall(name="nope", arguments={})).content
    assert "invalid JSON" in tools.dispatch(ToolCall(name="note", arguments="{bad")).content
    assert "raised RuntimeError" in tools.dispatch(ToolCall(name="boom", arguments={})).content


# -- ToolLoop: drives any adapter, provider-agnostic -------------------------

class FakeAdapter:
    """Scripted adapter: yields the queued completions, records dispatched
    tool results so the loop's feedback path is observable."""

    def __init__(self, completions):
        self.completions = list(completions)
        self.started = None
        self.fed = []

    def start(self, system, history, user_msg, toolset):
        self.started = (system, history, user_msg, toolset)

    def step(self):
        return self.completions.pop(0)

    def add_tool_results(self, results):
        self.fed.append(results)


def test_tool_loop_runs_tools_then_returns_final_text_and_sums_usage():
    surface = _Surface()
    toolset = ToolSet([surface.note])
    adapter = FakeAdapter(
        [
            Completion(
                tool_calls=[ToolCall(name="note", arguments={"text": "a"}, id="c1")],
                usage=Usage(input_tokens=100, output_tokens=20),
            ),
            Completion(text="all done", usage=Usage(input_tokens=150, output_tokens=30)),
        ]
    )
    out = ToolLoop(max_rounds=5).run(adapter, "sys", [], "go", toolset)
    assert out.text == "all done"
    assert out.rounds == 2
    assert out.usage == Usage(input_tokens=250, output_tokens=50)  # summed across rounds
    assert surface.calls == [("a", False)]
    assert adapter.fed[0][0].content.startswith("noted 'a'")
    assert adapter.started[0] == "sys"


def test_tool_loop_stops_at_round_budget():
    toolset = ToolSet([_Surface().note])
    forever = [Completion(tool_calls=[ToolCall(name="note", arguments={"text": "x"})]) for _ in range(10)]
    out = ToolLoop(max_rounds=3).run(FakeAdapter(forever), "s", [], "u", toolset)
    assert "maximum" in out.text
    assert out.rounds == 3


def test_tool_loop_empty_text_is_placeholder():
    out = ToolLoop(max_rounds=1).run(FakeAdapter([Completion(text="")]), "s", [], "u", ToolSet([]))
    assert out.text == "(no text)"


# -- provider resolution ------------------------------------------------------

def _config(**kw):
    base = dict(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="", orch_model="",
        runner_token="", data_dir=None,
    )
    base.update(kw)
    return Config(**base)


def test_build_adapter_resolves_provider_and_guards_credentials():
    from hive.llm.openai import OpenAIAdapter

    assert isinstance(build_adapter(_config(orch_provider="openai", openai_api_key="k")), OpenAIAdapter)
    assert isinstance(build_adapter(_config(orch_provider="gemini", orch_model="gemini-3", gemini_api_key="k")), GeminiAdapter)
    # auto infers from the model prefix
    assert isinstance(build_adapter(_config(orch_model="gpt-5.5", openai_api_key="k")), OpenAIAdapter)

    with pytest.raises(ValueError, match="No orchestrator provider"):
        build_adapter(_config(orch_provider="auto"))
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_adapter(_config(orch_provider="openai", openai_base_url="https://api.openai.com/v1"))
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_adapter(_config(orch_provider="gemini", orch_model="gemini-3"))


# -- Gemini adapter translation (no network) ---------------------------------

class _FakeFunctionCall:
    def __init__(self, name, args, id=""):
        self.name, self.args, self.id = name, args, id


class _FakeUsage:
    def __init__(self, prompt, candidates):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates


class _FakeResponse:
    def __init__(self, function_calls=None, text="", content=None, usage=None):
        self.function_calls = function_calls or []
        self.text = text
        self.candidates = [type("C", (), {"content": content})] if content is not None else []
        self.usage_metadata = usage


class _FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_contents = []

    def generate_content(self, model, contents, config):
        self.seen_contents.append(list(contents))
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.models = _FakeModels(responses)


class ScriptedGeminiAdapter(GeminiAdapter):
    def __init__(self, responses, **kw):
        super().__init__(api_key="k", model="gemini-3", **kw)
        self._responses = responses

    def _make_client(self, genai):
        return _FakeClient(self._responses)


def test_gemini_adapter_drives_tool_calls_then_text():
    surface = _Surface()
    toolset = ToolSet([surface.note])
    adapter = ScriptedGeminiAdapter(
        responses=[
            _FakeResponse(
                function_calls=[_FakeFunctionCall("note", {"text": "g"}, id="fc1")],
                content="MODEL_FUNC_CALL_CONTENT",
                usage=_FakeUsage(prompt=80, candidates=12),
            ),
            _FakeResponse(text="gemini done", usage=_FakeUsage(prompt=90, candidates=8)),
        ]
    )
    out = ToolLoop(max_rounds=5).run(adapter, "sys", [{"role": "user", "text": "hi"}], "go", toolset)
    assert out.text == "gemini done"
    assert out.usage == Usage(input_tokens=170, output_tokens=20)
    assert surface.calls == [("g", False)]
    # second turn resent the model's own function-call content plus a tool result
    second_turn = adapter._client.models.seen_contents[1]
    assert "MODEL_FUNC_CALL_CONTENT" in second_turn
    assert any(getattr(c, "role", "") == "tool" for c in second_turn)
