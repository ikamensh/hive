"""Fleet-aware build-agent choice: a hardcoded default backend that is
currently dead must not hard-block a project that never required it."""

from hive._control.agent_choice import available_backends, build_agent
from hive.models import AgentGrant, Project, Resource, ResourceUsability, Runner
from hive.persistence.store import MemoryStore


def _fleet(store: MemoryStore, *backends: str) -> Runner:
    """One online runner serving each backend as a usable resource."""
    runner = store.put(Runner(name="m", backends=list(backends)))
    for backend in backends:
        store.put(
            Resource(
                runner_id=runner.id,
                backend=backend,
                usability_status=ResourceUsability.usable,
            )
        )
    return runner


def test_available_backends_sees_only_usable_online_resources():
    store = MemoryStore()
    _fleet(store, "gemini-cli")
    store.put(  # a resource with no online runner is not available
        Resource(runner_id="ghost", backend="codex",
                 usability_status=ResourceUsability.usable)
    )
    assert available_backends(store, "default") == {"gemini-cli"}


def test_requested_backend_used_when_fleet_serves_it():
    store = MemoryStore()
    _fleet(store, "codex", "gemini-cli")
    assert build_agent(store, Project(name="p"), "codex", "") == ("codex", "")


def test_dead_default_falls_back_to_a_live_backend():
    """codex is the hardcoded build default; when it is dead but gemini-cli is
    online, the work goes to gemini-cli instead of blocking forever."""
    store = MemoryStore()
    _fleet(store, "gemini-cli")
    backend, model = build_agent(store, Project(name="p"), "codex", "gpt-5.4")
    assert backend == "gemini-cli"
    assert model == ""  # the codex-specific model is dropped on substitution


def test_substitution_respects_priority_order():
    store = MemoryStore()
    _fleet(store, "gemini-cli", "claude")  # codex absent; claude outranks gemini-cli
    backend, _ = build_agent(store, Project(name="p"), "codex", "")
    assert backend == "claude"


def test_grants_pin_the_backend_even_when_it_is_dead():
    """An explicit grant is a deliberate pin: honor it (and let it block) rather
    than silently rerouting to a backend the owner never allowed."""
    store = MemoryStore()
    _fleet(store, "gemini-cli")
    project = Project(name="p", agent_grants=[AgentGrant(backends=["codex"])])
    backend, _ = build_agent(store, project, "codex", "")
    assert backend == "codex"


def test_empty_fleet_leaves_the_request_unchanged():
    """No online capacity: keep the request so it waits as blocked_resources
    with a real reason, exactly as before this change."""
    store = MemoryStore()
    assert build_agent(store, Project(name="p"), "codex", "") == ("codex", "")
