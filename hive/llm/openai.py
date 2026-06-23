"""OpenAI-compatible chat-completions adapter: Hive's manual tool loop over
`/chat/completions`. Works against any OpenAI-shaped endpoint (set the base URL);
auto-selects the newest tool-capable model when none is configured."""

from __future__ import annotations

from hive.llm.core import Completion, ToolCall, ToolResult, ToolSet, Usage

# Substrings marking non-chat models we must not auto-select for tool calling.
# "-pro" is the reasoning tier (gpt-5.x-pro, o*-pro): Responses-API-only, so it
# 404s on /chat/completions with "not a chat model" — and it sorts newest, so it
# would otherwise win the auto-select.
MODEL_SKIP = (
    "-pro",
    "audio",
    "dall-e",
    "embedding",
    "image",
    "moderation",
    "realtime",
    "search",
    "speech",
    "transcribe",
    "tts",
)


def _usage(raw) -> Usage:
    if not raw:
        return Usage()
    return Usage(raw.get("prompt_tokens", 0) or 0, raw.get("completion_tokens", 0) or 0)


def _content_text(content) -> str:
    if isinstance(content, list):
        text = "\n".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        ).strip()
        return text or "(no text)"
    return content or "(no text)"


class OpenAIAdapter:
    def __init__(self, api_key: str, base_url: str, model: str = "") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        if "api.openai.com" in self.base_url and not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required when HIVE_ORCH_PROVIDER=openai.")
        self.messages: list[dict] = []
        self.tool_defs: list[dict] = []

    def start(self, system: str, history: list[dict], user_msg: str, toolset: ToolSet) -> None:
        if not self.model:
            self.model = self._select_model()
        self.tool_defs = toolset.openai_schemas()
        self.messages = [{"role": "system", "content": system}]
        for item in history:
            role = "assistant" if item["role"] == "model" else item["role"]
            self.messages.append({"role": role, "content": item["text"]})
        self.messages.append({"role": "user", "content": user_msg})

    def step(self) -> Completion:
        data = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "messages": self.messages,
                "tools": self.tool_defs,
                "tool_choice": "auto",
            },
        )
        message = data["choices"][0]["message"]
        usage = _usage(data.get("usage"))
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return Completion(text=_content_text(message.get("content")), usage=usage)
        assistant = {"role": "assistant", "tool_calls": tool_calls}
        if message.get("content") is not None:
            assistant["content"] = message["content"]
        self.messages.append(assistant)
        return Completion(
            usage=usage,
            tool_calls=[
                ToolCall(
                    id=call["id"],
                    name=call.get("function", {}).get("name", ""),
                    arguments=call.get("function", {}).get("arguments") or "{}",
                )
                for call in tool_calls
            ]
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for result in results:
            self.messages.append(
                {"role": "tool", "tool_call_id": result.call.id, "content": result.content}
            )

    # -- HTTP (overridden in tests) ------------------------------------------

    def _select_model(self) -> str:
        candidates = []
        for item in self._get("/models").get("data", []):
            model_id = item.get("id", "")
            lower = model_id.lower()
            if not lower.startswith(("gpt-", "o")) or any(p in lower for p in MODEL_SKIP):
                continue
            family = 1 if lower.startswith("gpt-") else 0
            candidates.append((family, int(item.get("created", 0) or 0), model_id))
        if not candidates:
            raise ValueError(
                "Could not auto-select an OpenAI model. Set HIVE_ORCH_MODEL to an "
                "OpenAI-compatible chat model that supports tool calling."
            )
        candidates.sort(reverse=True)
        return candidates[0][2]

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"
        response = httpx.request(
            method, f"{self.base_url}{path}", headers=headers, json=body, timeout=120.0
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenAI-compatible API error {response.status_code}: {response.text[:1000]}"
            )
        return response.json()
