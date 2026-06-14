import subprocess

import pytest

from hive import runner


def _completed(args, stdout=""):
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def test_checkout_uses_https_token_auth_for_github_ssh_urls(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    monkeypatch.setenv("HIVE_GH_TOKEN", "ghp_runner")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "clone"]:
            assert args[2] == "https://github.com/ikamensh/hive.git"
            assert "ghp_runner" not in args
            (tmp_path / "work" / "hive").mkdir(parents=True)
            env = kwargs["env"]
            assert env["GIT_CONFIG_COUNT"] == "1"
            assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
            assert "ghp_runner" not in env["GIT_CONFIG_VALUE_0"]
        if args[:2] == ["git", "symbolic-ref"]:
            return _completed(args, "origin/main\n")
        return _completed(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    path = runner.checkout("git@github.com:ikamensh/hive.git")

    assert path == tmp_path / "work" / "hive"
    clone = next(args for args, _kwargs in calls if args[:2] == ["git", "clone"])
    assert clone[2] == "https://github.com/ikamensh/hive.git"


def test_checkout_rewrites_existing_github_origin_before_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    monkeypatch.setenv("HIVE_GH_TOKEN", "ghp_runner")
    (tmp_path / "work" / "hive").mkdir(parents=True)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "symbolic-ref"]:
            return _completed(args, "origin/main\n")
        return _completed(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner.checkout("git@github.com:ikamensh/hive.git")

    git_calls = [args for args, _kwargs in calls]
    assert git_calls[0] == [
        "git",
        "remote",
        "set-url",
        "origin",
        "https://github.com/ikamensh/hive.git",
    ]
    assert git_calls[1] == ["git", "fetch", "origin"]


def test_runner_github_token_uses_allowed_user_for_gh_detection(monkeypatch):
    seen = []
    monkeypatch.delenv("HIVE_GH_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("HIVE_ALLOWED_GITHUB_USERS", "ikamensh,other")

    def fake_token(user):
        seen.append(user)
        return "ghp_allowed\n"

    monkeypatch.setattr("hive.github_repos.gh_token_for", fake_token)

    assert runner._runner_github_token() == "ghp_allowed"
    assert seen == ["ikamensh"]


def test_checkout_failure_message_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    monkeypatch.setattr(runner, "_runner_github_token", lambda: "")

    def fake_run(args, **kwargs):
        if args[:2] == ["git", "clone"]:
            raise subprocess.CalledProcessError(
                128,
                args,
                stderr="Permission denied (publickey).\nfatal: Could not read from remote repository.",
            )
        return _completed(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    with pytest.raises(runner.CheckoutError) as exc:
        runner.checkout("git@github.com:ikamensh/hive.git")

    message = str(exc.value)
    assert "checkout failed for git@github.com:ikamensh/hive.git" in message
    assert "Permission denied (publickey)" in message
    assert "HIVE_GH_TOKEN" in message
    assert "Command '[" not in message
