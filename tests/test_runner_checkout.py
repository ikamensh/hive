import subprocess
from pathlib import Path

import pytest

from hive.agents import backends
from hive.agents import BackendDiscovery, REGISTRY, discover_backend
from hive.runner import _daemon as runner


def _completed(args, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _discovery(name, installed=True):
    return BackendDiscovery(name=name, installed=installed, status="ok")


@pytest.mark.parametrize(
    ("backend", "binary", "version"),
    [
        ("codex", "codex", "codex-cli 0.139.0"),
    ],
)
def test_discover_backend_recognizes_realistic_claude_and_codex_versions(
    monkeypatch,
    backend,
    binary,
    version,
):
    calls = []

    def fake_which(name):
        return f"/opt/homebrew/bin/{name}" if name == binary else None

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _completed(args, stdout=f"{version}\n")

    monkeypatch.setattr(backends.shutil, "which", fake_which)
    monkeypatch.setattr(backends.subprocess, "run", fake_run)

    discovery = discover_backend(REGISTRY[backend])

    assert discovery.installed is True
    assert discovery.status == "ok"
    assert discovery.path == f"/opt/homebrew/bin/{binary}"
    assert discovery.version == version
    assert discovery.message == ""
    assert calls[0][0] == list(REGISTRY[backend].preflight)


def test_claude_discovery_reports_the_sdk_runtime_it_will_execute(tmp_path, monkeypatch):
    """An old system CLI must not hide the SDK binary actually used by sessions."""
    import importlib.util
    import sys
    from types import SimpleNamespace

    package = tmp_path / "claude_agent_sdk"
    bundled = package / "_bundled" / "claude"
    bundled.parent.mkdir(parents=True)
    bundled.write_text(f"#!{sys.executable}\nprint('2.1.259 (Claude Code)')\n")
    bundled.chmod(0o755)
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: (
        SimpleNamespace(origin=str(package / "__init__.py"))
        if name == "claude_agent_sdk" else real_find_spec(name)
    ))
    monkeypatch.setattr(backends.shutil, "which", lambda name: "/old/system/claude")

    discovery = discover_backend(REGISTRY["claude"])

    assert discovery.installed and discovery.status == "ok"
    assert discovery.path == str(bundled)
    assert discovery.version == "2.1.259 (Claude Code)"


def test_discovery_payload_can_limit_advertised_backends(monkeypatch):
    monkeypatch.setenv("HIVE_RUNNER_BACKENDS", "codex")
    monkeypatch.setattr(
        runner,
        "discover_backends",
        lambda: [_discovery("claude"), _discovery("codex"), _discovery("gemini-cli")],
    )

    detected, discoveries = runner.discovery_payload()

    assert detected == ["codex"]
    assert [d["name"] for d in discoveries] == ["claude", "codex", "gemini-cli"]


def test_discovery_payload_rejects_unknown_backend_filter(monkeypatch):
    monkeypatch.setenv("HIVE_RUNNER_BACKENDS", "codex,nope")
    monkeypatch.setattr(runner, "discover_backends", lambda: [_discovery("codex")])

    with pytest.raises(ValueError, match="unknown HIVE_RUNNER_BACKENDS"):
        runner.discovery_payload()


def test_detect_capabilities_recognizes_node_playwright(monkeypatch):
    def fake_which(name):
        return "/usr/bin/npx" if name == "npx" else None

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _completed(args, "Version 1.61.0\n")

    monkeypatch.setattr(runner.shutil, "which", fake_which)
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner.detect_capabilities() == ["browser"]
    assert calls == [["npx", "--no-install", "playwright", "--version"]]


def test_detect_capabilities_skips_browser_without_driver(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)

    assert runner.detect_capabilities() == []


def test_detect_capabilities_android_needs_working_adb(monkeypatch, tmp_path):
    """`android` is advertised only when the pack is genuinely usable: an SDK
    root under ANDROID_HOME whose platform-tools/adb executes. An SDK dir
    without adb (half-installed pack) must not be advertised."""
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None)
    sdk = tmp_path / "android-sdk"
    (sdk / "platform-tools").mkdir(parents=True)
    monkeypatch.setenv("ANDROID_HOME", str(sdk))

    assert runner.detect_capabilities() == []  # no adb yet

    adb = sdk / "platform-tools" / "adb"
    adb.write_text("#!/bin/sh\necho Android Debug Bridge version 35.0.0\n")
    adb.chmod(0o755)
    assert runner.detect_capabilities() == ["android"]


def test_upload_artifacts_skips_dependency_trees(monkeypatch, tmp_path):
    root = tmp_path / ".hive" / "artifacts"
    root.mkdir(parents=True)
    (root / "evidence.log").write_text("ok")
    dependency = root / "browser-runner" / "node_modules" / "playwright-core"
    dependency.mkdir(parents=True)
    (dependency / "index.js").write_text("generated")
    build = root / "dist"
    build.mkdir()
    (build / "bundle.js").write_text("generated")
    posted = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, content):
            posted.append((url, content))
            return FakeResponse()

    monkeypatch.setattr(runner.httpx, "Client", FakeClient)

    uploaded = runner._upload_artifacts("task-1", tmp_path, {}, None)

    assert uploaded == ["evidence.log"]
    assert [url for url, _content in posted] == ["/api/tasks/task-1/artifacts/evidence.log"]


def test_reset_task_scratch_removes_previous_runner_state(tmp_path):
    scratch = tmp_path / ".hive"
    artifacts = scratch / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "old.log").write_text("stale")
    issue = scratch / "issue-123"
    issue.mkdir()
    (issue / "ISSUE.md").write_text("stale issue")
    (scratch / "result.json").write_text("{}")
    (scratch / "operator-note.md").write_text("keep")

    runner._reset_task_scratch(tmp_path)

    assert not artifacts.exists()
    assert not issue.exists()
    assert not (scratch / "result.json").exists()
    assert (scratch / "operator-note.md").read_text() == "keep"


def test_git_auth_overlay_replaces_inherited_github_extraheader(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Hive Runner")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "Http.https://github.com/.ExtraHeader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "AUTHORIZATION: basic old")
    monkeypatch.setenv("GIT_CONFIG_KEY_2", "http.https://example.com/.extraheader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_2", "X-Trace: keep")

    overlay = runner._git_auth_overlay("ghp_new")

    assert overlay["GIT_CONFIG_COUNT"] == "4"
    assert overlay["GIT_CONFIG_KEY_0"] == "user.name"
    assert overlay["GIT_CONFIG_VALUE_0"] == "Hive Runner"
    assert overlay["GIT_CONFIG_KEY_1"] == "http.https://example.com/.extraheader"
    assert overlay["GIT_CONFIG_VALUE_1"] == "X-Trace: keep"
    assert overlay["GIT_CONFIG_KEY_2"] == "http.https://github.com/.extraheader"
    assert overlay["GIT_CONFIG_VALUE_2"] == ""
    assert overlay["GIT_CONFIG_KEY_3"] == "http.https://github.com/.extraheader"
    assert "ghp_new" not in overlay["GIT_CONFIG_VALUE_2"]
    assert "ghp_new" not in overlay["GIT_CONFIG_VALUE_3"]
    assert overlay["GIT_CONFIG_VALUE_3"] != "AUTHORIZATION: basic old"


def test_checkout_uses_https_token_auth_for_github_ssh_urls(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    monkeypatch.setenv("HIVE_GH_TOKEN", "ghp_runner")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.https://github.com/.extraheader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "AUTHORIZATION: bearer ambient")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "runner@example.invalid")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "clone"]:
            assert args[2] == "https://github.com/ikamensh/hive.git"
            assert "ghp_runner" not in args
            Path(args[3]).mkdir(parents=True)
            env = kwargs["env"]
            entries = [
                (env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
                for i in range(int(env["GIT_CONFIG_COUNT"]))
            ]
            assert ("user.email", "runner@example.invalid") in entries
            github_headers = [
                value
                for key, value in entries
                if key.lower() == "http.https://github.com/.extraheader"
            ]
            assert github_headers == ["", env["GIT_CONFIG_VALUE_2"]]
            assert "AUTHORIZATION: bearer ambient" not in github_headers
            assert "ghp_runner" not in env["GIT_CONFIG_VALUE_2"]
        if args[:2] == ["git", "for-each-ref"]:
            return _completed(args, "abc123 commit\trefs/remotes/origin/main\n")
        if args[:2] == ["git", "symbolic-ref"]:
            return _completed(args, "origin/main\n")
        return _completed(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    path = runner.checkout("git@github.com:ikamensh/hive.git")

    clone = next(args for args, _kwargs in calls if args[:2] == ["git", "clone"])
    assert path == Path(clone[3]) and path.parent == tmp_path / "work"
    assert clone[2] == "https://github.com/ikamensh/hive.git"


def test_checkout_rewrites_existing_github_origin_before_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    monkeypatch.setenv("HIVE_GH_TOKEN", "ghp_runner")
    (tmp_path / "work" / "hive").mkdir(parents=True)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "config"]:
            return _completed(args, "git@github.com:ikamensh/hive.git\n")
        if args[:2] == ["git", "for-each-ref"]:
            return _completed(args, "abc123 commit\trefs/remotes/origin/main\n")
        if args[:2] == ["git", "symbolic-ref"]:
            return _completed(args, "origin/main\n")
        return _completed(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner.checkout("git@github.com:ikamensh/hive.git")

    remote_calls = [args for args, _kwargs in calls if args[1] in ("remote", "fetch")]
    assert remote_calls == [
        ["git", "remote", "set-url", "origin", "https://github.com/ikamensh/hive.git"],
        ["git", "fetch", "origin"],
    ]


def test_checkout_restores_requested_origin_when_token_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    monkeypatch.setattr(runner, "_runner_github_token", lambda: "")
    (tmp_path / "work" / "hive").mkdir(parents=True)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["git", "config"]:
            return _completed(args, "https://github.com/ikamensh/hive.git\n")
        if args[:2] == ["git", "for-each-ref"]:
            return _completed(args, "abc123 commit\trefs/remotes/origin/main\n")
        if args[:2] == ["git", "symbolic-ref"]:
            return _completed(args, "origin/main\n")
        return _completed(args)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner.checkout("git@github.com:ikamensh/hive.git")

    remote_calls = [args for args, _kwargs in calls if args[1] in ("remote", "fetch")]
    assert remote_calls == [
        ["git", "remote", "set-url", "origin", "git@github.com:ikamensh/hive.git"],
        ["git", "fetch", "origin"],
    ]


def test_runner_github_token_uses_allowed_user_for_gh_detection(monkeypatch):
    seen = []
    monkeypatch.delenv("HIVE_GH_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("HIVE_ALLOWED_GITHUB_USERS", "ikamensh,other")

    def fake_token(user):
        seen.append(user)
        return "ghp_allowed\n"

    monkeypatch.setattr("hive._integrations.github_repos.gh_token_for", fake_token)

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


def test_checkout_of_empty_origin_yields_pushable_unborn_main(monkeypatch, tmp_path):
    """Greenfield intake: a brand-new project repo has no commits, yet checkout
    must produce a working tree whose first commit can be pushed to create
    origin's default branch (the scout writes spec files into an empty repo)."""
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)

    path = runner.checkout(str(origin))

    head = subprocess.run(
        ["git", "symbolic-ref", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()
    assert head == "refs/heads/main"
    (path / "mission.md").write_text("# mission\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.invalid", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=path, check=True)
    heads = subprocess.run(
        ["git", "ls-remote", "--heads", "origin"], cwd=path, capture_output=True, text=True
    ).stdout
    assert "refs/heads/main" in heads


def test_checkout_of_empty_origin_supports_a_named_branch(monkeypatch, tmp_path):
    """PR-mode work on a brand-new repo starts on the workstream branch even
    though origin has nothing to base it on."""
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)

    path = runner.checkout(str(origin), branch="hive/ws-1")

    head = subprocess.run(
        ["git", "symbolic-ref", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()
    assert head == "refs/heads/hive/ws-1"


def _git(path, *args):
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _seed_origin(path, owner):
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    (path / "owner.txt").write_text(owner)
    _git(path, "add", ".")
    _git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "seed")
    return path


def test_same_name_repositories_keep_separate_resumable_checkouts(monkeypatch, tmp_path):
    """Switching to another owner's app must leave interrupted work intact."""
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    alice = _seed_origin(tmp_path / "alice" / "app", "alice")
    bob = _seed_origin(tmp_path / "bob" / "app", "bob")
    alice_checkout = runner.checkout(str(alice))
    (alice_checkout / "unfinished.txt").write_text("Alice's interrupted work")

    bob_checkout = runner.checkout(str(bob))
    resumed = runner.checkout(str(alice), preserve=True)

    assert (resumed / "owner.txt").read_text() == "alice"
    assert (resumed / "unfinished.txt").read_text() == "Alice's interrupted work"
    assert resumed == alice_checkout and resumed != bob_checkout
    assert _git(resumed, "remote", "get-url", "origin") == str(alice)
    assert (bob_checkout / "owner.txt").read_text() == "bob"


@pytest.mark.parametrize("previous_name", [False, True])
def test_resume_rejects_a_changed_origin_without_touching_work(monkeypatch, tmp_path, previous_name):
    """A matching branch name alone is not proof that a checkout is ours."""
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    alice = _seed_origin(tmp_path / "alice" / "app", "alice")
    bob = _seed_origin(tmp_path / "bob" / "app", "bob")
    path = runner.checkout(str(alice))
    (path / "unfinished.txt").write_text("keep these edits")
    _git(path, "remote", "set-url", "origin", str(bob))
    if previous_name:
        path = path.rename(runner.WORKDIR / "app")

    with pytest.raises(runner.CheckoutError, match="different repository"):
        runner.checkout(str(alice), preserve=True)

    assert (path / "unfinished.txt").read_text() == "keep these edits"
    assert _git(path, "remote", "get-url", "origin") == str(bob)


def test_resume_moves_existing_basename_checkout_with_unfinished_work(monkeypatch, tmp_path):
    """Changing checkout names must preserve work made before the runner update."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(runner, "WORKDIR", work)
    origin = _seed_origin(tmp_path / "alice" / "app", "alice")
    previous = work / "app"
    _git(work, "clone", str(origin), str(previous))
    (previous / "owner.txt").write_text("work in progress")
    (previous / "unfinished.txt").write_text("keep these edits")

    resumed = runner.checkout(str(origin), preserve=True)

    assert (resumed / "owner.txt").read_text() == "work in progress"
    assert (resumed / "unfinished.txt").read_text() == "keep these edits"
    assert not previous.exists()
    assert _git(resumed, "remote", "get-url", "origin") == str(origin)


def test_github_transport_and_case_aliases_resume_the_same_checkout(monkeypatch, tmp_path):
    """GitHub auth transport and URL casing do not change repository identity."""
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    monkeypatch.setattr(runner, "_runner_github_token", lambda: "")
    origin = _seed_origin(tmp_path / "alice" / "app", "alice")
    ssh = "git@github.com:Alice/App.git"
    https = "https://github.com/alice/app.git"
    # Exercise real Git offline: both network URLs resolve to the local fixture.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    for i, url in enumerate((ssh, https)):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", f"url.{origin}.insteadOf")
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", url)

    path = runner.checkout(ssh)
    (path / "unfinished.txt").write_text("keep these edits")
    resumed = runner.checkout(https, preserve=True)

    assert resumed == path
    assert (resumed / "unfinished.txt").read_text() == "keep these edits"
