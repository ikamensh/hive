"""Agent backend registry: the single source of truth for the kodo coding
agents Hive can run.

Adding a backend means adding one `Backend` entry here; everything else (the
orchestrator's allowed-backend list, the runner's session factory, probe
support, capability detection) derives from `REGISTRY`. Session factories import
`kodo` lazily so the control plane can import this module without kodo present —
only the runner machine needs the agent CLIs installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

PROBE_MARKER = "HIVE_AGENT_PROBE_OK"


def _claude(model: str):
    from kodo.sessions.claude import ClaudeSession

    return ClaudeSession(model=model) if model else ClaudeSession()


def _cursor(model: str):
    from kodo.sessions.cursor import CursorSession

    return CursorSession(model=model) if model else CursorSession()


def _codex(model: str):
    from kodo.sessions.codex import CodexSession

    sandbox = "danger-full-access"
    return CodexSession(model=model, sandbox=sandbox) if model else CodexSession(sandbox=sandbox)


def _gemini_cli(model: str):
    from kodo.sessions.gemini_cli import GeminiCliSession

    return GeminiCliSession(model=model) if model else GeminiCliSession()


@dataclass(frozen=True)
class Backend:
    """One coding-agent backend. `make_session(model)` builds a kodo session
    (empty model = the backend's own default)."""

    name: str
    make_session: Callable[[str], object]


REGISTRY: dict[str, Backend] = {
    b.name: b
    for b in (
        Backend("claude", _claude),
        Backend("cursor", _cursor),
        Backend("codex", _codex),
        Backend("gemini-cli", _gemini_cli),
    )
}

BACKEND_NAMES: tuple[str, ...] = tuple(REGISTRY)


def make_session(backend: str, model: str = ""):
    """Build a kodo session for `backend`. Raises on an unknown backend rather
    than guessing — the registry is the contract."""
    if backend not in REGISTRY:
        raise ValueError(f"unknown backend {backend!r}; known: {BACKEND_NAMES}")
    return REGISTRY[backend].make_session(model)


def probe_instructions(backend: str) -> str:
    """The usability-probe prompt: prove the backend can read the repo and reply
    without mutating it, ending with PROBE_MARKER on its own line."""
    return (
        f"Hive agent usability probe for backend `{backend}`.\n"
        "Do not modify files, create commits, push branches, or change repository state.\n"
        "Inspect the repository only if needed, then reply with this exact marker on its own line:\n"
        f"{PROBE_MARKER}\n"
        "No markdown, no extra commentary."
    )
