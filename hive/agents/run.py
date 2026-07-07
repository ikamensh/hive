"""One call to run a coding agent: session + agent loop + structured result.

`run_agent` is the whole lifecycle of a single agent task — build the backend's
kodo session, drive it to completion in a working directory, validate the
structured result, and hand back the provider session handle so a follow-up can
resume the same conversation. Hive's runner daemon and standalone scripts use
this same entrypoint; long-running callers can hook `on_session` to keep a
cancel channel (`session.terminate()`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from hive.agents.backends import make_session
from hive.agents.results import AgentCallResult, ResultSpecLike, call_agent

DEFAULT_TIMEOUT_S = 3600.0
DEFAULT_MAX_TURNS = 100


def session_handle(session) -> str:
    """The provider-side session id, when the backend exposes one ("" otherwise).
    Passing it back as `resume_session` continues the same warm conversation."""
    handle = getattr(session, "session_id", None)
    if callable(handle):
        try:
            return handle() or ""
        except Exception:
            return ""
    return str(handle) if handle else ""


def run_agent(
    backend: str,
    instructions: str,
    workdir: Path,
    *,
    model: str = "",
    resume_session: str = "",
    result_spec: ResultSpecLike = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_turns: int = DEFAULT_MAX_TURNS,
    task_id: str = "",
    agent_name: str = "",
    on_session: Callable[[object], None] | None = None,
) -> AgentCallResult:
    """Run one agent task in `workdir` and return text, cost, and (with a
    `result_spec`) the validated structured payload. Requires the backend's CLI
    installed and logged in; `kodo` drives it."""
    from kodo.agent import Agent

    session = make_session(backend, model, resume_session)
    if on_session is not None:
        on_session(session)
    with Agent(session, max_turns=max_turns, timeout_s=timeout_s) as agent:
        result = call_agent(
            agent,
            instructions=instructions,
            workdir=workdir,
            result_spec=result_spec,
            task_id=task_id,
            agent_name=agent_name or backend,
        )
    result.session_handle = session_handle(session)
    return result
