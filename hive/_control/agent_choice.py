"""Which agent a build task runs on: the requested backend ∩ the live fleet.

`resolve_agent` (allowances) answers half the question — what the project's
*grants* permit. This adds the other half: what the fleet can actually serve
right now. Build work defaults to a strong-coder backend (RESOLVE_BACKEND =
"codex"), and that default is hardcoded, not chosen from what is online. So a
project that never required codex would sit `blocked_resources` forever the
moment codex's login lapsed, while an idle gemini-cli runner could have done
the work. `build_agent` substitutes a live backend in that case; grants and
`required_capabilities` (the deliberate ways to pin an agent) still win.
"""

from __future__ import annotations

from hive._control.allowances import permitted, resolve_agent
from hive.models import Project, Resource, Runner

# When a substitution is needed, prefer the strongest coder the fleet offers.
# Backends outside this list fall back to a deterministic alphabetical pick.
_BUILD_PRIORITY = ("codex", "claude", "cursor", "gemini-cli")


def available_backends(store, workspace_id: str) -> set[str]:
    """Backends an online runner currently offers with available quota."""
    online = {
        r.id for r in store.list(Runner, workspace_id=workspace_id) if r.online()
    }
    return {
        res.backend
        for res in store.list(Resource, workspace_id=workspace_id)
        if res.available() and res.runner_id in online
    }


def build_agent(store, project: Project, backend: str, model: str) -> tuple[str, str]:
    """The (backend, model) a build task should run on.

    The requested backend stands when the fleet can serve it, when nothing is
    online (preserve the old wait-for-capacity behaviour), or when grants pin
    the choice. Otherwise the fleet's preferred available backend is
    substituted (model reset — it belonged to the old backend). Grants decide
    last, exactly as before.
    """
    live = available_backends(store, project.workspace_id)
    if backend not in live and live:
        for candidate in (*_BUILD_PRIORITY, *sorted(live)):
            if candidate in live and permitted(project.agent_grants, candidate, ""):
                backend, model = candidate, ""
                break
    return resolve_agent(project.agent_grants, backend, model)
