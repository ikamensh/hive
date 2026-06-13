"""Unified LLM access: one tool-loop, one schema generator, one JSON parser,
thin per-provider adapters."""

from hive.llm.core import Completion, LLMAdapter, ToolCall, ToolLoop, ToolResult, ToolSet
from hive.llm.parsing import extract_json
from hive.llm.provider import build_adapter, resolve_provider

__all__ = [
    "Completion",
    "LLMAdapter",
    "ToolCall",
    "ToolLoop",
    "ToolResult",
    "ToolSet",
    "extract_json",
    "build_adapter",
    "resolve_provider",
]
