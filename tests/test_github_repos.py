import json

import pytest

from hive.integrations.github_repos import all_repos, clear_cache, parse_repo_ref, validate_repo


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_parse_repo_ref_accepts_common_shapes():
    assert parse_repo_ref("acme/atlas-api") == "acme/atlas-api"
    assert parse_repo_ref("git@github.com:acme/atlas-api.git") == "acme/atlas-api"
    assert parse_repo_ref("https://github.com/acme/atlas-api") == "acme/atlas-api"


def test_all_repos_prefers_user_oauth_token(monkeypatch):
    clear_cache()
    calls = {"gh": 0, "api": 0}

    def fake_run(args, **kwargs):
        calls["gh"] += 1
        proc = type("Proc", (), {})()
        proc.returncode = 0
        proc.stdout = "[]"
        proc.stderr = ""
        return proc

    def fake_get(*args, **kwargs):
        calls["api"] += 1
        return FakeResponse(
            [
                {
                    "full_name": "ikamensh/hive",
                    "ssh_url": "git@github.com:ikamensh/hive.git",
                    "clone_url": "https://github.com/ikamensh/hive.git",
                    "private": True,
                    "description": "",
                }
            ]
        )

    monkeypatch.setattr("hive.integrations.github_repos.subprocess.run", fake_run)
    monkeypatch.setattr("hive.integrations.github_repos.httpx.get", fake_get)

    repos = all_repos(github_login="ikamensh", user_token="gho_user", force=True)

    assert calls["gh"] == 0
    assert calls["api"] == 1
    assert repos[0]["full_name"] == "ikamensh/hive"


def test_all_repos_skips_gh_when_account_mismatch(monkeypatch):
    clear_cache()

    def fake_run(args, **kwargs):
        if args[:4] == ["gh", "api", "user", "-q"]:
            proc = type("Proc", (), {})()
            proc.returncode = 0
            proc.stdout = "ilya-covenance"
            proc.stderr = ""
            return proc
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr("hive.integrations.github_repos.subprocess.run", fake_run)
    monkeypatch.setattr(
        "hive.integrations.github_repos.httpx.get",
        lambda *a, **k: FakeResponse(
            [
                {
                    "full_name": "ikamensh/hive",
                    "ssh_url": "git@github.com:ikamensh/hive.git",
                    "clone_url": "https://github.com/ikamensh/hive.git",
                    "private": True,
                    "description": "",
                }
            ]
        ),
    )

    repos = all_repos(
        github_login="ikamensh",
        user_token="",
        config_token="ghp_server",
        force=True,
    )

    assert repos[0]["full_name"] == "ikamensh/hive"


def test_all_repos_uses_gh_cli_and_caches(monkeypatch):
    from hive.integrations.github_repos import clear_cache

    clear_cache()
    sample = [
        {
            "nameWithOwner": "acme/atlas-api",
            "sshUrl": "git@github.com:acme/atlas-api.git",
            "isPrivate": True,
            "description": "backend",
        }
    ]
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        proc = type("Proc", (), {})()
        proc.returncode = 0
        proc.stdout = json.dumps(sample)
        proc.stderr = ""
        return proc

    monkeypatch.setattr("hive.integrations.github_repos.subprocess.run", fake_run)
    monkeypatch.setattr("hive.integrations.github_repos._gh_active_login", lambda: "")
    all_repos(force=True)
    all_repos()

    assert calls["n"] == 1


def test_validate_repo_via_user_token(monkeypatch):
    monkeypatch.setattr(
        "hive.integrations.github_repos.httpx.get",
        lambda *a, **k: FakeResponse(
            {
                "full_name": "ikamensh/hive",
                "ssh_url": "git@github.com:ikamensh/hive.git",
                "private": True,
                "description": "",
            }
        ),
    )

    repo = validate_repo("ikamensh/hive", github_login="ikamensh", user_token="gho_user")

    assert repo["full_name"] == "ikamensh/hive"


def test_validate_repo_not_found(monkeypatch):
    def fake_run(args, **kwargs):
        proc = type("Proc", (), {})()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "Could not resolve to a Repository"
        return proc

    monkeypatch.setattr("hive.integrations.github_repos.subprocess.run", fake_run)

    with pytest.raises(LookupError, match="not found"):
        validate_repo("acme/missing")
