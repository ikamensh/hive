"""Google Gemini adapter via google-genai, driving function calling manually
(automatic function calling disabled) so the shared `ToolLoop` owns the cycle.

Tool callables are passed straight to the SDK, which builds function
declarations from their signatures and docstrings; with auto-calling off it
returns `function_calls` instead of executing them. We resend the model's own
function-call content each round so per-call ids / thought signatures survive.
"""

from __future__ import annotations

from hive.llm.core import Completion, ToolCall, ToolResult, ToolSet, Usage


def _usage(metadata) -> Usage:
    if metadata is None:
        return Usage()
    return Usage(
        getattr(metadata, "prompt_token_count", 0) or 0,
        getattr(metadata, "candidates_token_count", 0) or 0,
    )


class GeminiAdapter:
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required for the Hive orchestrator.")
        if not model.strip():
            raise ValueError("HIVE_ORCH_MODEL is required when HIVE_ORCH_PROVIDER=gemini.")
        self.api_key = api_key
        self.model = model
        self._client = None
        self._contents: list = []
        self._callables: list = []
        self._system = ""

    def start(self, system: str, history: list[dict], user_msg: str, toolset: ToolSet) -> None:
        from google import genai
        from google.genai import types

        self._system = system
        self._callables = toolset.callables()
        self._contents = [
            types.Content(role=m["role"], parts=[types.Part(text=m["text"])]) for m in history
        ]
        self._contents.append(types.Content(role="user", parts=[types.Part(text=user_msg)]))
        self._client = self._make_client(genai)

    def _make_client(self, genai):
        """Client construction seam: tests inject a fake client."""
        return genai.Client(api_key=self.api_key)

    def step(self) -> Completion:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.model,
            contents=self._contents,
            config=types.GenerateContentConfig(
                system_instruction=self._system,
                tools=self._callables,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        usage = _usage(getattr(response, "usage_metadata", None))
        calls = response.function_calls or []
        if not calls:
            return Completion(text=response.text or "(no text)", usage=usage)
        self._contents.append(response.candidates[0].content)
        return Completion(
            usage=usage,
            tool_calls=[
                ToolCall(id=call.id or "", name=call.name, arguments=dict(call.args or {}))
                for call in calls
            ],
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        from google.genai import types

        parts = [
            types.Part.from_function_response(name=r.call.name, response={"result": r.content})
            for r in results
        ]
        self._contents.append(types.Content(role="tool", parts=parts))
