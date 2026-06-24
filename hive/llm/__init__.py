"""Unified LLM access: one tool-loop, one schema generator, one JSON parser,
thin per-provider adapters."""

from hive.llm._core import (
    Completion,
    LLMAdapter,
    LoopResult,
    ProviderUnavailable,
    ToolCall,
    ToolLoop,
    ToolResult,
    ToolSet,
    Usage,
)
from hive.llm._parsing import extract_json
from hive.llm._provider import build_adapter, build_adapters, candidate_providers, resolve_provider

__all__ = [
    "Completion",
    "LLMAdapter",
    "LoopResult",
    "ProviderUnavailable",
    "ToolCall",
    "ToolLoop",
    "ToolResult",
    "ToolSet",
    "Usage",
    "extract_json",
    "build_adapter",
    "build_adapters",
    "candidate_providers",
    "resolve_provider",
]
