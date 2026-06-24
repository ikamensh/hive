from urllib.parse import parse_qs, urlparse

import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from hive.config.settings import Config
from hive.models import Machine, Project
from hive.persistence.store import FileStore, MemoryStore
from hive._control.supervisor import Supervisor


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def make_client(store: MemoryStore, **overrides):
    from hive.api import create_app

    gh_token = overrides.pop("gh_token", "")
    config = Config(
        gcp_project="",
        gcs_bucket="",
        gh_token=gh_token,
        gemini_api_key="",
        orch_model="",
        runner_token="t",
        data_dir=None,
        machine_name="chief-test",
        **overrides,
    )
    supervisor = Supervisor(
        store,
        lambda p, e: None,
        workspace_id=config.workspace_id,
        machine_name=config.machine_name,
    )
    return TestClient(create_app(store, supervisor, config)), config


def test_dev_auth_bootstraps_user_and_workspace():
    store = MemoryStore()
    client, config = make_client(store)

    response = client.get("/api/auth/me")
    me = response.json()

    assert me["user"]["github_login"] == "ikamensh"
    assert me["workspace"]["id"] == config.workspace_id
    assert "hive_session" in response.headers["set-cookie"]


def test_chief_does_not_register_itself_as_a_machine():
    # Machines are runner hosts the user recognizes. A chief is a process, not a
    # durable machine — persisting one left a permanent offline card per chief
    # host (every ephemeral container hostname). The machines view is empty until
    # a runner registers.
    store = MemoryStore()
    _client, config = make_client(store)

    assert store.list(Machine, workspace_id=config.workspace_id) == []


def test_dev_auth_disables_github_oauth_routes():
    store = MemoryStore()
    client, _config = make_client(store)

    start = client.get("/api/auth/github/start", follow_redirects=False)
    callback = client.get("/api/auth/github/callback?code=abc&state=bad", follow_redirects=False)

    assert start.status_code == 404
    assert callback.status_code == 404
    assert start.json()["detail"] == "GitHub auth is not enabled"
    assert callback.json()["detail"] == "GitHub auth is not enabled"


def test_dev_auth_concurrent_file_store(tmp_path):
    """FileStore remains available for tests; production runtime uses managed state."""
    from hive.api import create_app

    store = FileStore(tmp_path / "store")
    config = Config(
        gcp_project="",
        gcs_bucket="",
        gh_token="",
        gemini_api_key="",
        orch_model="",
        runner_token="t",
        data_dir=tmp_path,
        machine_name="chief-test",
    )
    supervisor = Supervisor(store, lambda p, e: None, machine_name=config.machine_name)
    client = TestClient(create_app(store, supervisor, config))

    def poll_auth():
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        return response.json()["user"]["github_login"]

    def poll_projects():
        response = client.get("/api/projects")
        assert response.status_code == 200
        return len(response.json())

    with ThreadPoolExecutor(max_workers=20) as pool:
        auth_hits = list(pool.map(lambda _: poll_auth(), range(30)))
        project_hits = list(pool.map(lambda _: poll_projects(), range(20)))

    assert all(login == "ikamensh" for login in auth_hits)
    assert all(count == 0 for count in project_hits)
    user_file = tmp_path / "store" / "users" / "github:ikamensh.json"
    assert user_file.is_file()
    assert json.loads(user_file.read_text())["github_login"] == "ikamensh"


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
        "hive._integrations.auth.httpx.post",
        lambda *a, **k: FakeResponse({"access_token": "gho_test"}),
    )
    monkeypatch.setattr(
        "hive._integrations.auth.httpx.get",
        lambda *a, **k: FakeResponse({"login": "ikamensh", "name": "Ikamen"}),
    )

    callback = client.get(
        f"/api/auth/github/callback?code=abc&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code in {302, 307}
    assert "hive_session" in callback.headers["set-cookie"]
    assert client.get("/api/auth/me").json()["user"]["github_login"] == "ikamensh"


def test_github_callback_stores_access_token(monkeypatch):
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
        "hive._integrations.auth.httpx.post",
        lambda *a, **k: FakeResponse({"access_token": "gho_test"}),
    )
    monkeypatch.setattr(
        "hive._integrations.auth.httpx.get",
        lambda *a, **k: FakeResponse({"login": "ikamensh", "name": "Ikamen"}),
    )

    client.get(
        f"/api/auth/github/callback?code=abc&state={state}",
        follow_redirects=False,
    )

    from hive.models import User

    user = store.list(User)[0]
    assert user.github_access_token == "gho_test"


def test_github_repos_uses_server_token_when_gh_unavailable(monkeypatch):
    from hive._integrations.github_repos import clear_cache

    clear_cache()
    store = MemoryStore()
    client, _config = make_client(store, gh_token="ghp_server")

    def gh_fail(args, **kwargs):
        proc = type("Proc", (), {})()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "not logged in"
        return proc

    sample = [
        {
            "full_name": "acme/demo",
            "ssh_url": "git@github.com:acme/demo.git",
            "clone_url": "https://github.com/acme/demo.git",
            "private": False,
            "description": "",
        }
    ]

    monkeypatch.setattr("hive._integrations.github_repos.subprocess.run", gh_fail)
    monkeypatch.setattr(
        "hive._integrations.github_repos.httpx.get",
        lambda *a, **k: FakeResponse(sample),
    )

    response = client.get("/api/github/repos")

    assert response.status_code == 200
    assert response.json()[0]["full_name"] == "acme/demo"


def test_github_validate_repo(monkeypatch):
    from hive._integrations.github_repos import clear_cache

    clear_cache()
    store = MemoryStore()
    client, _config = make_client(store, gh_token="ghp_server")

    def fake_run(args, **kwargs):
        proc = type("Proc", (), {})()
        if args[:3] == ["gh", "repo", "view"]:
            proc.returncode = 0
            proc.stdout = json.dumps(
                {
                    "nameWithOwner": "acme/demo",
                    "sshUrl": "git@github.com:acme/demo.git",
                    "isPrivate": False,
                    "description": "",
                }
            )
        elif args[:4] == ["gh", "api", "user", "-q"]:
            proc.returncode = 0
            proc.stdout = "ikamensh"
        else:
            proc.returncode = 1
            proc.stdout = ""
            proc.stderr = "unexpected"
        proc.stderr = proc.stderr if hasattr(proc, "stderr") else ""
        return proc

    monkeypatch.setattr("hive._integrations.github_repos.subprocess.run", fake_run)

    response = client.get("/api/github/repos/validate?ref=acme/demo")

    assert response.status_code == 200
    assert response.json()["ssh_url"] == "git@github.com:acme/demo.git"


def test_github_repos_without_gh_or_token(monkeypatch):
    from hive._integrations.github_repos import clear_cache

    clear_cache()
    store = MemoryStore()
    client, _config = make_client(store, gh_token="")

    def gh_fail(args, **kwargs):
        proc = type("Proc", (), {})()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "not logged in"
        return proc

    monkeypatch.setattr("hive._integrations.github_repos.subprocess.run", gh_fail)
    monkeypatch.setattr("hive._integrations.github_repos._gh_token", lambda: "")

    response = client.get("/api/github/repos")

    assert response.status_code == 503
    assert "gh auth login" in response.json()["detail"]


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
        "hive._integrations.auth.httpx.post",
        lambda *a, **k: FakeResponse({"access_token": "gho_test"}),
    )
    monkeypatch.setattr(
        "hive._integrations.auth.httpx.get",
        lambda *a, **k: FakeResponse({"login": "someone-else", "name": "Nope"}),
    )

    callback = client.get(
        f"/api/auth/github/callback?code=abc&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 403
