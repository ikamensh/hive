"""Self-onboarding a laptop: enrollment tokens and the machine claim.

A member mints a one-hour token in the web UI, pastes `hive enroll` on the
laptop; the token buys runner credentials, and the first register claims the
machine for that member — no admin in the loop, no GCP access needed.
"""

from hive.models import Machine
from hive.persistence.store import MemoryStore

from test_users_roles import RUNNER_HEADERS, login, make_app


def test_member_exchanges_token_for_creds_and_machine_is_claimed():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    alice_h, _alice = login(auth, "alice")
    bob_h, bob = login(auth, "bob")
    # Even a resource provider self-onboards — that's the whole point of the role.
    client.patch(f"/api/users/{bob.id}", json={"role": "resource_provider"}, headers=alice_h)

    minted = client.post("/api/enroll-tokens", headers=bob_h)
    assert minted.status_code == 200
    assert "hive enroll" in minted.json()["command"]
    assert minted.json()["token"] in minted.json()["command"]

    exchanged = client.post("/api/enroll", json={"token": minted.json()["token"]})
    assert exchanged.status_code == 200
    creds = exchanged.json()
    assert creds["runner_token"] == "t"
    assert creds["owner_user_id"] == bob.id
    assert creds["chief_urls"] == ["http://testserver"]

    # The runner daemon registers with HIVE_RUNNER_OWNER from runner.env.
    registered = client.post(
        "/api/runners/register",
        json={"name": "bobs-laptop", "backends": [], "owner_user_id": creds["owner_user_id"]},
        headers=RUNNER_HEADERS,
    )
    assert registered.status_code == 200
    machine = store.get(Machine, registered.json()["machine_id"])
    assert machine.owner_user_id == bob.id


def test_enroll_rejects_garbage_and_session_tokens():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    _bob_h, bob = login(auth, "bob")

    assert client.post("/api/enroll", json={"token": "junk"}).status_code == 401
    # A session token is signed with the same secret but has the wrong type —
    # a leaked cookie must not double as an enrollment credential.
    session = auth.session_token(bob)
    assert client.post("/api/enroll", json={"token": session}).status_code == 401


def test_cli_enroll_writes_runner_env_on_linux(monkeypatch, tmp_path):
    """`hive enroll` on a non-mac host materializes runner.env with the
    exchanged credentials and the owner claim — the same file the daemon and
    the mac installer use."""
    import sys

    from hive import cli

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {
                "chief_urls": ["http://chief:8000"],
                "runner_token": "runner-secret",
                "gh_token": "gh-secret",
                "workspace_id": "default",
                "owner_user_id": "github:bob",
            }

        def raise_for_status(self):
            return None

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HIVE_RUNNER_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("HIVE_BASIC_AUTH", raising=False)
    monkeypatch.setattr(cli, "load_stored_config", lambda *a, **k: {})

    cli.main(["enroll", "--url", "http://chief:8000/", "--token", "tok", "--name", "box"])

    content = (tmp_path / "runner.env").read_text()
    assert "HIVE_URL=http://chief:8000\n" in content
    assert "HIVE_RUNNER_TOKEN=runner-secret\n" in content
    assert "HIVE_RUNNER_NAME=box\n" in content
    assert "HIVE_GH_TOKEN=gh-secret\n" in content
    assert "HIVE_RUNNER_OWNER=github:bob\n" in content


def test_register_owner_claims_once_and_never_steals():
    store = MemoryStore()
    client, auth, _sup = make_app(store)
    _alice_h, alice = login(auth, "alice")
    _bob_h, bob = login(auth, "bob")

    body = {"name": "shared-box", "backends": [], "owner_user_id": bob.id}
    first = client.post("/api/runners/register", json=body, headers=RUNNER_HEADERS).json()
    assert store.get(Machine, first["machine_id"]).owner_user_id == bob.id

    # A later register naming another owner (stale env, re-enrolled token)
    # leaves the established owner in place.
    body["owner_user_id"] = alice.id
    second = client.post("/api/runners/register", json=body, headers=RUNNER_HEADERS).json()
    assert second["machine_id"] == first["machine_id"]
    assert store.get(Machine, first["machine_id"]).owner_user_id == bob.id
