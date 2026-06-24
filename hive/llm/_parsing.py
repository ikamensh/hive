"""Tolerant JSON extraction from LLM prose. Shared by the orchestrator-adjacent
flows and the spec critique, which both ask models for a JSON payload wrapped in
chatter or a ```json fence."""

from __future__ import annotations

import json
import re


def extract_json(text: str):
    """Parse the last ```json fence, or fall back to the outermost bracket span."""
    fences = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if fences:
        return json.loads(fences[-1])
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise ValueError(f"no JSON found in model output: {text[:500]!r}")
    end = max(text.rfind("]"), text.rfind("}"))
    return json.loads(text[start : end + 1])
