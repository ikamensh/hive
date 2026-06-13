"""Shared constants for local agent discovery and usability probes."""

from __future__ import annotations

SUPPORTED_BACKENDS = ("claude", "cursor", "codex", "gemini-cli")
PROBE_MARKER = "HIVE_AGENT_PROBE_OK"


def probe_instructions(backend: str) -> str:
    return (
        f"Hive agent usability probe for backend `{backend}`.\n"
        "Do not modify files, create commits, push branches, or change repository state.\n"
        "Inspect the repository only if needed, then reply with this exact marker on its own line:\n"
        f"{PROBE_MARKER}\n"
        "No markdown, no extra commentary."
    )
