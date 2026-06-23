"""Resolve the configured orchestrator provider(s) and build their adapters.

`HIVE_ORCH_PROVIDER=auto` infers the provider from `HIVE_ORCH_MODEL`'s prefix;
with no model pinned it returns *every* provider that has a credential, ordered,
so the orchestrator can fall back when the first is out of quota or down (see
`ProviderUnavailable`). An explicit provider pins exactly one — no fallback,
because the user chose it. Adapter constructors enforce their own credential
requirements, so a misconfiguration fails loudly here, not mid-conversation.
"""

from __future__ import annotations

from hive.llm.core import LLMAdapter
from hive.llm.gemini import GeminiAdapter
from hive.llm.openai import OpenAIAdapter

# Gemini has no list-and-pick auto-select like OpenAI's, so auto-fallback needs a
# concrete model. The strongest tool-caller the key serves (see llm/model_intel).
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"


def candidate_providers(config) -> list[str]:
    """Ordered providers to try, preferred first."""
    provider = (config.orch_provider or "auto").strip().lower()
    if provider not in {"auto", "openai", "gemini"}:
        raise ValueError("HIVE_ORCH_PROVIDER must be one of: auto, openai, gemini.")
    if provider != "auto":
        return [provider]
    model = config.orch_model.strip().lower()
    if model.startswith("gemini"):
        return ["gemini"]
    if model.startswith(("gpt-", "o")):
        return ["openai"]
    order = [
        p
        for p, has_key in (("openai", config.openai_api_key), ("gemini", config.gemini_api_key))
        if has_key.strip()
    ]
    if not order:
        raise ValueError(
            "No orchestrator provider is configured. Set OPENAI_API_KEY for OpenAI-compatible "
            "orchestration, or set HIVE_ORCH_PROVIDER=gemini with GEMINI_API_KEY."
        )
    return order


def resolve_provider(config) -> str:
    """The single preferred provider (the first candidate)."""
    return candidate_providers(config)[0]


def _adapter_for(provider: str, config) -> LLMAdapter:
    if provider == "openai":
        return OpenAIAdapter(config.openai_api_key, config.openai_base_url, config.orch_model.strip())
    return GeminiAdapter(config.gemini_api_key, config.orch_model.strip() or DEFAULT_GEMINI_MODEL)


def build_adapters(config) -> list[LLMAdapter]:
    """Adapters to try in order; the orchestrator falls back down the list."""
    return [_adapter_for(p, config) for p in candidate_providers(config)]


def build_adapter(config) -> LLMAdapter:
    return build_adapters(config)[0]
