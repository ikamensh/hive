"""Resolve the configured orchestrator provider and build its adapter.

`HIVE_ORCH_PROVIDER=auto` infers the provider from `HIVE_ORCH_MODEL`'s prefix,
then from whichever API key is present. The adapter constructors enforce their
own credential requirements, so a misconfiguration fails loudly here rather than
mid-conversation.
"""

from __future__ import annotations

from hive.llm.core import LLMAdapter
from hive.llm.gemini import GeminiAdapter
from hive.llm.openai import OpenAIAdapter


def resolve_provider(config) -> str:
    provider = (config.orch_provider or "auto").strip().lower()
    if provider not in {"auto", "openai", "gemini"}:
        raise ValueError("HIVE_ORCH_PROVIDER must be one of: auto, openai, gemini.")
    if provider != "auto":
        return provider
    model = config.orch_model.strip().lower()
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith(("gpt-", "o")):
        return "openai"
    if config.openai_api_key.strip():
        return "openai"
    if config.gemini_api_key.strip():
        return "gemini"
    raise ValueError(
        "No orchestrator provider is configured. Set OPENAI_API_KEY for OpenAI-compatible "
        "orchestration, or set HIVE_ORCH_PROVIDER=gemini with GEMINI_API_KEY."
    )


def build_adapter(config) -> LLMAdapter:
    provider = resolve_provider(config)
    if provider == "openai":
        return OpenAIAdapter(config.openai_api_key, config.openai_base_url, config.orch_model.strip())
    return GeminiAdapter(config.gemini_api_key, config.orch_model.strip())
