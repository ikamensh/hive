"""Coding-agent interactions — usable on its own, no other hive imports.

Everything about the agent CLIs installed on a machine (claude, cursor, codex,
gemini-cli), independent of any hive service:

- registry & discovery: which backends exist, which are installed
  (`REGISTRY`, `discover_backends`);
- running: one call to execute an agent task in a directory, with an optional
  structured Pydantic result the agent must write and repair (`run_agent`,
  `ResultSpec`); `make_session`/`call_agent` are the underlying steps;
- probing: prove a backend truly works here by running it against a throwaway
  repo (`probe_backend`);
- accounting: classify failures (`classify_failure`), read the provider's own
  usage gauge without spending quota (`collect_usage`), parse reset times out
  of rate-limit messages (`parse_reset_hint`).

Session factories import `kodo` lazily, so importing this package (e.g. on
hive's chief) needs no agent CLIs installed. `python -m hive.agents` surveys
the current machine. Demos: `demos/agents/`.
"""

from hive.agents.backends import (
    BACKEND_NAMES,
    PROBE_MARKER,
    REGISTRY,
    Backend,
    BackendDiscovery,
    backend_licensing,
    classify_failure,
    detected_backend_names,
    discover_backend,
    discover_backends,
    make_session,
    probe_instructions,
)
from hive.agents.probe import ensure_probe_repo, probe_backend, validate_probe_result
from hive.agents.results import (
    RESULT_PATH,
    AgentCallResult,
    ResultSpec,
    ResultSpecLike,
    as_result_spec,
    call_agent,
)
from hive.agents.run import run_agent, session_handle
from hive.agents.usage import collect_usage, parse_reset_hint

__all__ = [
    "BACKEND_NAMES",
    "PROBE_MARKER",
    "REGISTRY",
    "RESULT_PATH",
    "AgentCallResult",
    "Backend",
    "BackendDiscovery",
    "ResultSpec",
    "ResultSpecLike",
    "as_result_spec",
    "backend_licensing",
    "call_agent",
    "classify_failure",
    "collect_usage",
    "detected_backend_names",
    "discover_backend",
    "discover_backends",
    "ensure_probe_repo",
    "make_session",
    "parse_reset_hint",
    "probe_backend",
    "probe_instructions",
    "run_agent",
    "session_handle",
    "validate_probe_result",
]
