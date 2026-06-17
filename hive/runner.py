"""Runner daemon: registers with the control plane, long-polls for tasks,
executes them with a kodo agent in a local checkout, reports results.

Run directly: `python -m hive.runner`. Configuration via environment:
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

import httpx

from hive.backends import (
    BACKEND_NAMES,
    PROBE_MARKER,
    detected_backend_names,
    discover_backends,
    make_session,
)
from hive.agent_results import call_agent, result_spec_for_task
from hive.machine import machine_metadata
from hive.models import DEFAULT_WORKSPACE_ID

MACHINE_METADATA = machine_metadata()
HIVE_URL = os.environ.get("HIVE_URL", "http://localhost:8000")
HIVE_BASIC_AUTH = os.environ.get("HIVE_BASIC_AUTH", "")  # "user:pass" when behind Caddy
RUNNER_TOKEN = os.environ.get("HIVE_RUNNER_TOKEN", "dev-token")
WORKSPACE_ID = os.environ.get("HIVE_WORKSPACE_ID", DEFAULT_WORKSPACE_ID)
MACHINE_ID = os.environ.get("HIVE_MACHINE_ID", "")
MACHINE_NAME = os.environ.get("HIVE_MACHINE_NAME", "")
MACHINE_TYPE = MACHINE_METADATA["machine_type"]
MACHINE_OS = MACHINE_METADATA["machine_os"]
MACHINE_ARCH = MACHINE_METADATA["machine_arch"]
MACHINE_KIND = MACHINE_METADATA["machine_kind"]
RUNNER_NAME = os.environ.get("HIVE_RUNNER_NAME", socket.gethostname())
WORKDIR = Path(os.environ.get("HIVE_RUNNER_WORKDIR", "~/hive-work")).expanduser()
TASK_TIMEOUT_S = float(os.environ.get("HIVE_TASK_TIMEOUT_S", "3600"))
CANCEL_POLL_S = 5.0  # how often a running task checks for an operator cancel request

log = logging.getLogger("hive.runner")

EXHAUSTED_PATTERNS = re.compile(
    r"rate.?limit|quota|usage.?limit|plan.?limit|too many requests|429\b|subscription|billing",
    re.IGNORECASE,
)
SKIP_ARTIFACT_PARTS = {
    ".cache",
    ".git",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
GITHUB_URL = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"(?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


class CheckoutError(RuntimeError):
    """A user-facing checkout failure with stderr and without secret material."""


def detect_backends() -> list[str]:
    return discovery_payload()[0]


def discovery_payload() -> tuple[list[str], list[dict]]:
    discoveries = discover_backends()
    filtered = _filter_backend_discoveries(discoveries)
    return detected_backend_names(filtered), [asdict(d) for d in discoveries]


def _filter_backend_discoveries(discoveries):
    requested = [name.strip() for name in os.environ.get("HIVE_RUNNER_BACKENDS", "").split(",") if name.strip()]
    if not requested:
        return discoveries
    unknown = sorted(set(requested) - set(BACKEND_NAMES))
    if unknown:
        raise ValueError(
            "unknown HIVE_RUNNER_BACKENDS entries "
            f"{', '.join(unknown)}; known backends: {', '.join(BACKEND_NAMES)}"
        )
    allowed = set(requested)
    return [discovery for discovery in discoveries if discovery.name in allowed]


def detect_capabilities() -> list[str]:
    capabilities = []
    if shutil.which("docker"):
        try:
            docker = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
            if docker.returncode == 0:
                capabilities.append("docker")
        except (OSError, subprocess.SubprocessError):
            pass
    if _has_browser_driver():
        capabilities.append("browser")
    return capabilities


def _has_browser_driver() -> bool:
    try:
        if importlib.util.find_spec("playwright") is not None:
            return True
    except Exception:
        pass
    if not shutil.which("npx"):
        return False
    try:
        probe = subprocess.run(
            ["npx", "--no-install", "playwright", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _github_repo(repo_url: str) -> str:
    match = GITHUB_URL.match(repo_url.strip())
    return match.group("repo") if match else ""


def _runner_github_token() -> str:
    if token := (os.environ.get("HIVE_GH_TOKEN") or os.environ.get("GH_TOKEN") or "").strip():
        return token
    preferred = (
        os.environ.get("HIVE_GITHUB_LOGIN", "").strip()
        or os.environ.get("HIVE_ALLOWED_GITHUB_USERS", "ikamensh").split(",")[0].strip()
    )
    if not preferred:
        return ""
    try:
        from hive.github_repos import gh_token_for
    except Exception:
        return ""
    try:
        return gh_token_for(preferred).strip()
    except Exception:
        return ""


def _git_auth_overlay(token: str) -> dict[str, str]:
    if not token:
        return {}
    target_key = "http.https://github.com/.extraheader"
    existing: list[tuple[str, str]] = []
    try:
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    for i in range(count):
        key = os.environ.get(f"GIT_CONFIG_KEY_{i}", "")
        value = os.environ.get(f"GIT_CONFIG_VALUE_{i}", "")
        if not key or key == target_key:
            continue
        existing.append((key, value))
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # GitHub CLI can install a global extraheader. An empty value resets the
    # multi-valued header list before Hive adds the one it wants.
    entries = [*existing, (target_key, ""), (target_key, f"AUTHORIZATION: basic {basic}")]
    overlay = {"GIT_CONFIG_COUNT": str(len(entries))}
    for i, (key, value) in enumerate(entries):
        overlay[f"GIT_CONFIG_KEY_{i}"] = key
        overlay[f"GIT_CONFIG_VALUE_{i}"] = value
    return overlay


def _checkout_plan(repo_url: str) -> tuple[str, dict[str, str]]:
    repo = _github_repo(repo_url)
    if not repo:
        return repo_url, {}
    token = _runner_github_token()
    if not token:
        return repo_url, {}
    return f"https://github.com/{repo}.git", _git_auth_overlay(token)


def _with_env(overlay: dict[str, str]) -> dict[str, str] | None:
    return {**os.environ, **overlay} if overlay else None


@contextlib.contextmanager
def _git_auth_environment(repo_url: str):
    _checkout_url, overlay = _checkout_plan(repo_url)
    if not overlay:
        yield
        return
    previous = {key: os.environ.get(key) for key in overlay}
    os.environ.update(overlay)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _tail(text: str | bytes | None, limit: int = 1600) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return text.strip()[-limit:]


def _format_checkout_failure(
    *,
    repo_url: str,
    branch: str,
    args: list[str],
    returncode: int | str,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
) -> str:
    target = f"{repo_url} ({branch})" if branch else repo_url
    lines = [
        f"checkout failed for {target}",
        f"{shlex.join(['git', *args])} exited {returncode}",
    ]
    detail = _tail(stderr) or _tail(stdout)
    if detail:
        lines.append(detail)
    if _github_repo(repo_url):
        lines.append(
            "For private GitHub repos, set HIVE_GH_TOKEN on the runner or configure "
            "runner git/SSH access for the same GitHub account."
        )
    return "\n".join(lines)


def _run_checkout_git(
    args: list[str],
    *,
    cwd: Path | None,
    timeout: float,
    env: dict[str, str] | None,
    repo_url: str,
    branch: str,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise CheckoutError(
            _format_checkout_failure(
                repo_url=repo_url,
                branch=branch,
                args=args,
                returncode=exc.returncode,
                stdout=exc.stdout,
                stderr=exc.stderr,
            )
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CheckoutError(
            _format_checkout_failure(
                repo_url=repo_url,
                branch=branch,
                args=args,
                returncode=f"timeout after {exc.timeout}s",
                stdout=exc.stdout,
                stderr=exc.stderr,
            )
        ) from exc


def checkout(repo_url: str, branch: str = "", fresh_branch: bool = False) -> Path:
    """Fresh-ish checkout: clone once, fetch, then hard-reset to the target.

    With no branch, resets to the origin default (work that lands on main).
    With a branch, checks out that branch — existing on origin (verify/fix of
    PR-mode work) or freshly created off the default (the first PR-mode work
    task, which then pushes it). For issue-solving resolve retries, `fresh_branch`
    means an existing issue branch is first backed up and reset to the current
    default branch so the new attempt does not build on stale rejected work."""
    checkout_url, auth_overlay = _checkout_plan(repo_url)
    env = _with_env(auth_overlay)
    slug = checkout_url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    path = WORKDIR / slug
    if path.exists():
        _run_checkout_git(
            ["remote", "set-url", "origin", checkout_url],
            cwd=path,
            timeout=60,
            env=env,
            repo_url=checkout_url,
            branch=branch,
        )
        _run_checkout_git(
            ["fetch", "origin"], cwd=path, timeout=300, env=env, repo_url=checkout_url, branch=branch
        )
    else:
        WORKDIR.mkdir(parents=True, exist_ok=True)
        _run_checkout_git(
            ["clone", checkout_url, str(path)],
            cwd=None,
            timeout=600,
            env=env,
            repo_url=checkout_url,
            branch=branch,
        )
    default_head = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
        cwd=path, capture_output=True, text=True, env=env,
    ).stdout.strip() or "origin/main"
    _run_checkout_git(
        ["reset", "--hard"],
        cwd=path,
        timeout=60,
        env=env,
        repo_url=checkout_url,
        branch=branch,
    )
    _run_checkout_git(
        ["clean", "-fd"],
        cwd=path,
        timeout=60,
        env=env,
        repo_url=checkout_url,
        branch=branch,
    )
    if branch:
        on_origin = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", branch],
            cwd=path, capture_output=True, text=True, timeout=60, env=env,
        ).stdout.strip()
        if on_origin:
            _run_checkout_git(
                ["fetch", "origin", branch],
                cwd=path,
                timeout=120,
                env=env,
                repo_url=checkout_url,
                branch=branch,
            )
        if fresh_branch and on_origin:
            backup = f"{branch}-previous-{int(time.time())}"
            _run_checkout_git(
                ["push", "origin", f"origin/{branch}:refs/heads/{backup}"],
                cwd=path,
                timeout=120,
                env=env,
                repo_url=checkout_url,
                branch=branch,
            )
            _run_checkout_git(
                ["checkout", "-B", branch, default_head],
                cwd=path,
                timeout=60,
                env=env,
                repo_url=checkout_url,
                branch=branch,
            )
            _run_checkout_git(
                ["push", "--force-with-lease", "origin", f"{branch}:{branch}"],
                cwd=path,
                timeout=120,
                env=env,
                repo_url=checkout_url,
                branch=branch,
            )
        else:
            base = f"origin/{branch}" if on_origin else default_head
            _run_checkout_git(
                ["checkout", "-B", branch, base],
                cwd=path,
                timeout=60,
                env=env,
                repo_url=checkout_url,
                branch=branch,
            )
    else:
        _run_checkout_git(
            ["reset", "--hard", default_head],
            cwd=path,
            timeout=60,
            env=env,
            repo_url=checkout_url,
            branch=branch,
        )
    _run_checkout_git(
        ["clean", "-fd"], cwd=path, timeout=60, env=env, repo_url=checkout_url, branch=branch
    )
    return path


def _git(args: list[str], cwd: Path, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def run_preflight(project_dir: Path) -> dict:
    """Runner self-check for issue solving: prove this host can actually do the
    agent-facing GitHub work — push a branch to the repo and use `gh` — so a
    misconfigured runner is caught before a big run instead of mid-fix. Pushes a
    throwaway branch and deletes it; runs `gh auth status`. Leaves no trace."""
    results: list[tuple[str, bool, str]] = []

    gh = subprocess.run(
        ["gh", "auth", "status"], cwd=project_dir, capture_output=True, text=True, timeout=30
    )
    results.append(("gh auth status", gh.returncode == 0, (gh.stderr or gh.stdout).strip()[-500:]))

    branch = f"hive/preflight-{int(time.time())}"
    push_ok, detail = False, ""
    try:
        _git(["checkout", "-B", branch], project_dir, 30)
        commit = _git(
            ["-c", "user.email=preflight@hive.invalid", "-c", "user.name=Hive Preflight",
             "commit", "--allow-empty", "-m", "hive preflight"],
            project_dir, 30,
        )
        if commit.returncode != 0:
            detail = (commit.stderr or commit.stdout).strip()[-500:]
        else:
            push = _git(["push", "-u", "origin", branch], project_dir)
            push_ok = push.returncode == 0
            detail = (push.stderr or push.stdout).strip()[-500:]
            if push_ok:
                _git(["push", "origin", "--delete", branch], project_dir)
    except subprocess.SubprocessError as exc:
        detail = str(exc)
    results.append(("git push to origin", push_ok, detail))

    ok = all(passed for _, passed, _ in results)
    lines = [f"{'PASS' if p else 'FAIL'} {name}: {info}" for name, p, info in results]
    return {"text": "RUNNER PREFLIGHT\n" + "\n".join(lines), "is_error": not ok}


def prepare_issue_workspace(project_dir: Path, task: dict, headers: dict, auth) -> None:
    """Issue solving: materialize the issue context into the checkout under
    `.hive/issue-<n>/` (ISSUE.md + attachments), git-excluded so the agent never
    commits it. Attachments are pulled from the control plane (which downloaded
    them from GitHub at scan time with repo credentials), so the runner needs no
    GitHub auth of its own."""
    number = task.get("issue_number") or 0
    if not number:
        return
    base = project_dir / ".hive" / f"issue-{number}"
    attachments = base / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    (base / "ISSUE.md").write_text(task.get("issue_doc", ""))
    exclude = project_dir / ".git" / "info" / "exclude"
    if exclude.exists() and ".hive/" not in exclude.read_text():
        with exclude.open("a") as fh:
            fh.write("\n.hive/\n")
    names = task.get("issue_attachments") or []
    if not names:
        return
    client = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=60.0, auth=auth)
    for name in names:
        try:
            response = client.get(f"/api/tasks/{task['id']}/attachments/{name}")
            response.raise_for_status()
            (attachments / name).write_bytes(response.content)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("attachment fetch failed (%s): %s", name, exc)


def _reset_task_scratch(project_dir: Path) -> None:
    scratch = project_dir / ".hive"
    shutil.rmtree(scratch / "artifacts", ignore_errors=True)
    result = scratch / "result.json"
    if result.exists():
        result.unlink()
    if scratch.is_dir():
        for path in scratch.glob("issue-*"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


def _upload_trace(task_id: str, log_file, headers: dict, auth) -> None:
    """Best-effort: ship the kodo JSONL run trace to the control plane so the
    operator can inspect what the agent actually did."""
    if not log_file or not Path(log_file).exists():
        return
    try:
        data = Path(log_file).read_bytes()
        httpx.Client(base_url=HIVE_URL, headers=headers, timeout=30.0, auth=auth).post(
            f"/api/tasks/{task_id}/trace", content=data
        )
    except (httpx.HTTPError, OSError) as exc:
        log.warning("trace upload failed for %s: %s", task_id, exc)


def _upload_artifacts(task_id: str, project_dir: Path, headers: dict, auth) -> list[str]:
    root = project_dir / ".hive" / "artifacts"
    if not root.is_dir():
        return []
    client = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=60.0, auth=auth)
    uploaded: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        name = path.relative_to(root).as_posix()
        if any(part in SKIP_ARTIFACT_PARTS for part in Path(name).parts):
            log.info("skipping generated artifact dependency/cache file %s", name)
            continue
        try:
            response = client.post(f"/api/tasks/{task_id}/artifacts/{name}", content=path.read_bytes())
            response.raise_for_status()
            uploaded.append(name)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("artifact upload failed for %s (%s): %s", task_id, name, exc)
    return uploaded


def execute(task: dict, headers: dict, auth) -> dict:
    from kodo import log as kodo_log
    from kodo.agent import Agent

    try:
        project_dir = checkout(
            task["repo"],
            task.get("branch", ""),
            fresh_branch=bool(task.get("fresh_branch")),
        )
    except CheckoutError as exc:
        return {"text": str(exc), "is_error": True}
    except subprocess.SubprocessError as exc:
        return {"text": f"checkout failed: {exc}", "is_error": True}
    _reset_task_scratch(project_dir)
    if task["kind"] == "preflight":
        with _git_auth_environment(task["repo"]):
            return run_preflight(project_dir)
    prepare_issue_workspace(project_dir, task, headers, auth)

    with _git_auth_environment(task["repo"]):
        kodo_log.init(kodo_log.RunDir.create(project_dir))  # capture a per-task JSONL trace
        session = make_session(
            task["backend"],
            task.get("model", ""),
            task.get("session_handle", ""),
        )
        cancelled = threading.Event()
        stop_watch = threading.Event()

        def watch_for_cancel() -> None:
            # Poll the task; on an operator cancel request, terminate the session,
            # which unblocks Agent.run in its worker thread.
            watcher = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=15.0, auth=auth)
            while not stop_watch.wait(CANCEL_POLL_S):
                try:
                    state = watcher.get(f"/api/tasks/{task['id']}").json()
                except httpx.HTTPError:
                    continue
                if state.get("cancel_requested"):
                    cancelled.set()
                    session.terminate()
                    return

        watcher_thread = threading.Thread(target=watch_for_cancel, daemon=True)
        watcher_thread.start()
        try:
            with Agent(session, max_turns=100, timeout_s=TASK_TIMEOUT_S) as agent:
                result = call_agent(
                    agent,
                    task,
                    project_dir,
                    result_spec_for_task(task["kind"]),
                )
        except BaseException:
            if cancelled.is_set():
                return {"text": "Task cancelled by operator.", "cancelled": True}
            raise
        finally:
            stop_watch.set()
            _upload_trace(task["id"], kodo_log.get_log_file(), headers, auth)
            _upload_artifacts(task["id"], project_dir, headers, auth)

    if cancelled.is_set():
        return {"text": "Task cancelled by operator.", "cancelled": True}
    text = result.text
    is_error = result.is_error
    if task["kind"] == "probe":
        text, is_error = validate_probe_result(
            project_dir,
            text,
            is_error,
            backend=task.get("backend", ""),
        )
    session_handle = ""
    session_id = getattr(session, "session_id", None)
    if callable(session_id):
        try:
            session_handle = session_id() or ""
        except Exception:
            session_handle = ""
    elif session_id:
        session_handle = str(session_id)
    return {
        "text": text,
        "is_error": is_error,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "structured_result": result.structured_result,
        "structured_result_error": result.structured_result_error,
        "resource_exhausted": bool(is_error and EXHAUSTED_PATTERNS.search(text)),
        "session_handle": session_handle,
    }


def validate_probe_result(
    project_dir: Path,
    text: str,
    is_error: bool,
    *,
    backend: str = "",
) -> tuple[str, bool]:
    diagnostics = []
    if PROBE_MARKER not in text:
        diagnostics.append(f"probe marker {PROBE_MARKER!r} was not found in the agent reply")
    if backend == "codex" and "--full-auto" in text and "deprecated" in text.lower():
        diagnostics.append(
            "codex-cli is installed, but the kodo Codex wrapper invoked the deprecated "
            "`--full-auto` mode and returned no assistant message; update the wrapper to "
            "use the current `codex exec --sandbox ...` interface"
        )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if dirty:
        diagnostics.append(f"probe left repository changes:\n{dirty}")
    if diagnostics:
        return f"{text}\n\nHIVE PROBE FAILED:\n" + "\n".join(f"- {d}" for d in diagnostics), True
    if not is_error:
        text = f"{text}\n\nHIVE PROBE PASSED: backend replied with {PROBE_MARKER} and left the repo clean."
    return text, is_error


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--list-backends"]:
        import json

        detected, discoveries = discovery_payload()
        print(
            json.dumps(
                {
                    "supported": list(BACKEND_NAMES),
                    "detected": detected,
                    "discoveries": discoveries,
                    "message": (
                        "supported agents detected"
                        if detected
                        else "no supported agents found; install or log in to claude, cursor, codex, or gemini-cli"
                    ),
                },
                indent=2,
            )
        )
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    headers = {"X-Hive-Token": RUNNER_TOKEN, "X-Hive-Workspace": WORKSPACE_ID}
    auth = tuple(HIVE_BASIC_AUTH.split(":", 1)) if HIVE_BASIC_AUTH else None
    client = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=40.0, auth=auth)

    def register(client: httpx.Client, *, boot: bool = False) -> tuple[str, list[str]]:
        backends, discoveries = discovery_payload()
        capabilities = detect_capabilities()
        runner_id = client.post(
            "/api/runners/register",
            json={
                "name": RUNNER_NAME,
                "backends": backends,
                "machine_id": MACHINE_ID,
                "machine_name": MACHINE_NAME,
                "machine_type": MACHINE_TYPE,
                "machine_os": MACHINE_OS,
                "machine_arch": MACHINE_ARCH,
                "machine_kind": MACHINE_KIND,
                "boot": boot,
                "discoveries": discoveries,
                "capabilities": capabilities,
                "auto_probe": True,
            },
        ).raise_for_status().json()["runner_id"]
        return runner_id, backends

    runner_id, backends = register(client, boot=True)
    if backends:
        log.info("registered as %s (%s) with backends %s", RUNNER_NAME, runner_id, backends)
    else:
        log.warning("registered as %s (%s) with no supported agents on PATH", RUNNER_NAME, runner_id)

    def heartbeat() -> None:
        # Keeps last_seen fresh while a long task blocks the main loop;
        # otherwise the control plane declares us offline and orphans the task.
        hb = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=15.0, auth=auth)
        while True:
            time.sleep(30)
            try:
                register(hb)
            except httpx.HTTPError:
                pass

    threading.Thread(target=heartbeat, daemon=True).start()

    while True:
        try:
            response = client.post(f"/api/runners/{runner_id}/poll")
            if response.status_code == 404:
                runner_id, backends = register(client)
                continue
            task = response.raise_for_status().json().get("task")
            if not task:
                continue
            log.info("executing %s task %s on %s", task["kind"], task["id"], task["repo"])
            result = execute(task, headers, auth)
            log.info("task %s done (error=%s)", task["id"], result.get("is_error"))
            client.post(f"/api/tasks/{task['id']}/result", json=result)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("transient error: %s — retrying in 10s", exc)
            time.sleep(10)


if __name__ == "__main__":
    main()
