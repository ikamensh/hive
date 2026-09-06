"""Goal-first intake uses the HTTP contract and persists intent before dispatch."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from hive.api import create_app
from hive.config.settings import Config
from hive._control.supervisor import Supervisor
from hive.models import AgentConversation, Project, Resource, Runner, Task
from hive.persistence import MemoryStore


def test_goal_retries_preserve_original_spec_and_queue_one_scout():
    """A retried high-level goal starts one conversation without losing old specs."""
    store = MemoryStore()
    supervisor = Supervisor(store, lambda project, events: None)
    client = TestClient(create_app(store, supervisor, Config(gcp_project="", gcs_bucket="",
        gemini_api_key="", orch_model="", data_dir=None, gh_token="", runner_token="test")))
    runner = store.put(Runner(name="Laptop", backends=["codex"]))
    store.put(Resource(runner_id=runner.id, backend="codex", usability_status="usable"))
    project = client.post("/api/projects", json={"name": "Garden", "spec_text": "Original garden mission"}).json()
    project_id = project["id"]
    client.patch(f"/api/projects/{project_id}", json={"spec_repo": "https://example.com/garden.git"})
    goal = "Help a beginner know what to plant and when."
    def start():
        return client.post(f"/api/projects/{project_id}/intake/start", json={"goal": goal})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start(), range(2)))
    assert all(response.status_code == 200 for response in responses), [r.json() for r in responses]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert store.get(Project, project_id).initial_spec == "Original garden mission"
    assert store.get(Project, project_id).pending_iteration_goal == goal
    tasks = store.list(Task, project_id=project_id)
    assert len(tasks) == 1
    assert goal in tasks[0].instructions
    assert "Original garden mission" in tasks[0].instructions
    conversation = store.get(AgentConversation, responses[0].json()["id"])
    assert any(turn.get("text") == goal for turn in conversation.transcript)
    conflicting = client.post(f"/api/projects/{project_id}/intake/start", json={"goal": "Replace the app with a game"})
    assert conflicting.status_code == 409
    assert len(store.list(Task, project_id=project_id)) == 1
