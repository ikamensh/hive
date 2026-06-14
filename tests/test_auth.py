from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from hive.config import Config
from hive.models import Machine, Project
from hive.store import MemoryStore
from hive.supervisor import Supervisor


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def make_client(store: MemoryStore, **overrides):
    from hive.api import create_app

    config = Config(
        gcp_project="",
        gcs_bucket="",
        gh_token="",
        gemini_api_key="",
        orch_model="",
        runner_token="t",
        data_dir=None,
        machine_name="control-test",
        **overrides,
    )
    supervisor = Supervisor(
        store,
        lambda p, e: None,
        workspace_id=config.workspace_id,
        machine_name=config.machine_name,
    )
    return TestClient(create_app(store, supervisor, config)), config


def test_dev_auth_bootstraps_user_workspace_and_machine():
    store = MemoryStore()
    client, config = make_client(store)

    response = client.get("/api/auth/me")
    me = response.json()

    assert me["user"]["github_login"] == "ikamensh"
    assert me["workspace"]["id"] == config.workspace_id
    assert "hive_session" in response.headers["set-cookie"]
    machines = store.list(Machine, workspace_id=config.workspace_id)
    assert [m.name for m in machines] == ["control-test"]


def test_project_routes_are_workspace_scoped():
    store = MemoryStore()
    client, _config = make_client(store)
    store.put(Project(workspace_id="other", name="foreign"))

    mine = client.post("/api/projects", json={"name": "mine"}).json()
    projects = client.get("/api/projects").json()

    assert [p["name"] for p in projects] == ["mine"]
    assert client.get(f"/api/projects/{mine['id']}").status_code == 200
    assert client.get(f"/api/projects/{store.list(Project, workspace_id='other')[0].id}").status_code == 404


def test_github_login_accepts_allowlisted_user(monkeypatch):
    store = MemoryStore()
    client, _config = make_client(
        store,
        auth_mode="github",
        github_client_id="client-id",
        github_client_secret="client-secret",
        auth_secret="auth-secret",
        public_url="http://testserver",
    )
    start = client.get("/api/auth/github/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr(
        "hive.auth.httpx.post",
        lambda *a, **k: FakeResponse({"access_token": "gho_test"}),
    )
    monkeypatch.setattr(
        "hive.auth.httpx.get",
        lambda *a, **k: FakeResponse({"login": "ikamensh", "name": "Ikamen"}),
    )

    callback = client.get(
        f"/api/auth/github/callback?code=abc&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code in {302, 307}
    assert "hive_session" in callback.headers["set-cookie"]
    assert client.get("/api/auth/me").json()["user"]["github_login"] == "ikamensh"


def test_github_login_rejects_non_allowlisted_user(monkeypatch):
    store = MemoryStore()
    client, _config = make_client(
        store,
        auth_mode="github",
        github_client_id="client-id",
        github_client_secret="client-secret",
        auth_secret="auth-secret",
        public_url="http://testserver",
    )
    start = client.get("/api/auth/github/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr(
        "hive.auth.httpx.post",
        lambda *a, **k: FakeResponse({"access_token": "gho_test"}),
    )
    monkeypatch.setattr(
        "hive.auth.httpx.get",
        lambda *a, **k: FakeResponse({"login": "someone-else", "name": "Nope"}),
    )

    callback = client.get(
        f"/api/auth/github/callback?code=abc&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 403
