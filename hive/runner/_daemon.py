"""Runner daemon: registers with the chief, long-polls for tasks,
executes them with a kodo agent in a local checkout, reports results.

Run directly: `python -m hive.runner`. Configuration via environment:
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
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

from hive.agents import (
    BACKEND_NAMES,
    classify_failure,
    collect_usage,
    detected_backend_names,
    discover_backends,
    ensure_probe_repo,
    parse_reset_hint,
    run_agent,
    validate_probe_result,
)
from hive.runner._agent_results import result_spec_for_task
from hive.worker import WorkerConfig, WorkerLoop, parse_urls, update_available
from hive.fleet import machine_metadata
from hive.models import DEFAULT_WORKSPACE_ID
from hive.version import get_version

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
RUNNER_OWNER = os.environ.get("HIVE_RUNNER_OWNER", "")  # set by `hive enroll`; claims the machine
WORKDIR = Path(os.environ.get("HIVE_RUNNER_WORKDIR", "~/hive-work")).expanduser()
TASK_TIMEOUT_S = float(os.environ.get("HIVE_TASK_TIMEOUT_S", "3600"))
CANCEL_POLL_S = 5.0  # how often a running task checks for an operator cancel request
# Chief discovery: HIVE_URL may list several candidates (comma-separated); more
# are learned from the chief's register response and persisted here.
CHIEF_STATE_PATH = (
    Path(os.environ.get("HIVE_RUNNER_STATE_DIR", "~/.config/hive")).expanduser() / "chiefs.json"
)
# Self-update (set by install_mac_runner.sh for dedicated service clones, never
# for dev checkouts or the VM, where push.sh owns the tree): between tasks the
# daemon exits when origin/main is ahead; the service wrapper pulls + respawns.
SELF_UPDATE = os.environ.get("HIVE_RUNNER_SELF_UPDATE", "") == "1"
UPDATE_CHECK_INTERVAL_S = 900.0
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_VERSION = get_version()

log = logging.getLogger("hive.runner._daemon")

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
GITHUB_EXTRAHEADER_KEY = "http.https://github.com/.extraheader"


class CheckoutError(RuntimeError):
    """A user-facing checkout failure with stderr and without secret material."""


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


USAGE_REFRESH_S = 900.0  # collectors are cheap but not free (one HTTPS GET / rollout scan)
_usage_cache: dict[str, tuple[float, dict]] = {}
_usage_lock = threading.Lock()


def refresh_usage(backend: str) -> dict | None:
    """Collect a fresh usage snapshot for `backend` (after a task ran there)
    and remember it for the register heartbeat."""
    snapshot = collect_usage(backend)
    with _usage_lock:
        _usage_cache[backend] = (time.time(), snapshot or {})
    return snapshot


def usage_snapshots(backends: list[str]) -> dict[str, dict]:
    """Per-backend usage snapshots for the register payload, refreshed at most
    every USAGE_REFRESH_S — the account gauges move slowly between tasks while
    the heartbeat fires every 30s."""
    out: dict[str, dict] = {}
    for backend in backends:
        with _usage_lock:
            collected_at, snapshot = _usage_cache.get(backend, (0.0, {}))
        if time.time() - collected_at > USAGE_REFRESH_S:
            snapshot = refresh_usage(backend) or {}
        if snapshot:
            out[backend] = snapshot
    return out


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
        from hive._integrations.github_repos import gh_token_for
    except Exception:
        return ""
    try:
        return gh_token_for(preferred).strip()
    except Exception:
        return ""


def _git_auth_overlay(token: str) -> dict[str, str]:
    if not token:
        return {}
    existing: list[tuple[str, str]] = []
    try:
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    for i in range(count):
        key = os.environ.get(f"GIT_CONFIG_KEY_{i}", "")
        value = os.environ.get(f"GIT_CONFIG_VALUE_{i}", "")
        if not key or key.lower() == GITHUB_EXTRAHEADER_KEY:
            continue
        existing.append((key, value))
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # GitHub CLI can install a global extraheader. An empty value resets the
    # multi-valued header list before Hive adds the one it wants.
    entries = [
        *existing,
        (GITHUB_EXTRAHEADER_KEY, ""),
        (GITHUB_EXTRAHEADER_KEY, f"AUTHORIZATION: basic {basic}"),
    ]
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
    remote_refs = subprocess.run(
        ["git", "for-each-ref", "refs/remotes/origin"],
        cwd=path, capture_output=True, text=True, env=env,
    ).stdout.strip()
    if not remote_refs:
        # Empty origin (a brand-new project repo): nothing to reset to. Park
        # HEAD on an unborn branch; the first push creates it on origin.
        _run_checkout_git(
            ["checkout", "-B", branch or "main"],
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
        # No branch = "work that lands on main": put HEAD *on* the default branch,
        # not merely reset its content. A bare `reset --hard origin/main` leaves the
        # checkout on whatever local branch a prior task left it on (e.g. an issue
        # branch), so the agent's `git push HEAD` would land on that stale branch
        # instead of main — silently misdirecting refresh/intake commits.
        default_branch = default_head.split("/", 1)[1] if "/" in default_head else default_head
        _run_checkout_git(
            ["checkout", "-B", default_branch, default_head],
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


def _checkout_facts(repo_dir: Path) -> dict | None:
    """Read the git drift facts for one checkout: origin URL, HEAD, branch,
    ahead/behind vs its upstream, and whether the tree is dirty. Best-effort —
    returns None if the directory is not a usable git repo. Reports observed
    state only; never mutates the checkout."""
    try:
        origin = _git(["remote", "get-url", "origin"], repo_dir, 15)
        if origin.returncode != 0:
            return None
        head = _git(["rev-parse", "HEAD"], repo_dir, 15).stdout.strip()
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo_dir, 15).stdout.strip()
        dirty = bool(_git(["status", "--porcelain"], repo_dir, 30).stdout.strip())
        ahead = behind = 0
        counts = _git(["rev-list", "--left-right", "--count", "@{upstream}...HEAD"], repo_dir, 30)
        if counts.returncode == 0 and counts.stdout.strip():
            parts = counts.stdout.split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return {
        "repo": origin.stdout.strip(),
        "exists": True,
        "head_sha": head,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty,
    }


def collect_checkouts() -> list[dict]:
    """Git facts for every checkout under WORKDIR, for the heartbeat payload.
    Lets the chief track where each project physically exists and
    whether machine-local work has drifted from the remote."""
    if not WORKDIR.is_dir():
        return []
    facts = []
    for child in sorted(WORKDIR.iterdir()):
        if child.is_dir() and (child / ".git").exists():
            if observed := _checkout_facts(child):
                facts.append(observed)
    return facts


def run_preflight(project_dir: Path) -> dict:
    """Runner self-check for issue solving: prove this host can actually do the
    agent-facing GitHub work — push a branch to the repo and use `gh` — so a
    misconfigured runner is caught before a big run instead of mid-fix. Pushes a
    throwaway branch and deletes it; checks `gh` auth plus repo issue-commenting
    permission. Leaves no trace."""
    results: list[tuple[str, bool, str]] = []

    gh = subprocess.run(
        ["gh", "auth", "status"], cwd=project_dir, capture_output=True, text=True, timeout=30
    )
    results.append(("gh auth status", gh.returncode == 0, (gh.stderr or gh.stdout).strip()[-500:]))

    gh_repo = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner,viewerPermission,hasIssuesEnabled"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    issue_comment_ok = False
    issue_comment_detail = (gh_repo.stderr or gh_repo.stdout).strip()[-500:]
    if gh_repo.returncode == 0:
        try:
            repo_info = json.loads(gh_repo.stdout or "{}")
        except json.JSONDecodeError as exc:
            issue_comment_detail = f"could not parse gh repo view JSON: {exc}"
        else:
            permission = str(repo_info.get("viewerPermission") or "").upper()
            has_issues = bool(repo_info.get("hasIssuesEnabled"))
            issue_comment_ok = has_issues and permission in {"TRIAGE", "WRITE", "MAINTAIN", "ADMIN"}
            issue_comment_detail = (
                f"{repo_info.get('nameWithOwner') or 'repo'} permission={permission or 'unknown'} "
                f"issues={'enabled' if has_issues else 'disabled'}"
            )
    results.append(("gh issue commenting auth", issue_comment_ok, issue_comment_detail))

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
    commits it. Attachments are pulled from the chief (which downloaded
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
    """Best-effort: ship the kodo JSONL run trace to the chief so the
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

    if task["kind"] == "probe":
        # Probes run in a self-built local repo, never a chief path.
        project_dir = ensure_probe_repo(WORKDIR)
    else:
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
        cancelled = threading.Event()
        stop_watch = threading.Event()

        def watch_for_cancel(session) -> None:
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

        def start_cancel_watch(session) -> None:
            threading.Thread(target=watch_for_cancel, args=(session,), daemon=True).start()

        try:
            result = run_agent(
                task["backend"],
                str(task.get("instructions") or ""),
                project_dir,
                model=task.get("model", ""),
                resume_session=task.get("session_handle", ""),
                result_spec=result_spec_for_task(task["kind"]),
                timeout_s=TASK_TIMEOUT_S,
                task_id=str(task.get("id") or ""),
                agent_name=str(task.get("kind") or "agent"),
                on_session=start_cancel_watch,
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
    failure = classify_failure(text, is_error=is_error)
    return {
        "text": text,
        "is_error": is_error,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "structured_result": result.structured_result,
        "structured_result_error": result.structured_result_error,
        "resource_exhausted": failure == "exhausted",
        "auth_blocked": failure == "auth",
        "session_handle": result.session_handle,
        # Parsed on the runner because clock-time messages are rendered in
        # this machine's timezone; 0.0 = the message named no reset time.
        "reset_at_hint": parse_reset_hint(text) if failure == "exhausted" else 0.0,
        "usage_snapshot": refresh_usage(task["backend"]) or {},
    }


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

    def payload(boot: bool) -> dict:
        backends, discoveries = discovery_payload()
        if boot and not backends:
            log.warning("no supported agents on PATH — registering anyway for visibility")
        return {
            "name": RUNNER_NAME,
            "backends": backends,
            "machine_id": MACHINE_ID,
            "machine_name": MACHINE_NAME,
            "machine_type": MACHINE_TYPE,
            "machine_os": MACHINE_OS,
            "machine_arch": MACHINE_ARCH,
            "machine_kind": MACHINE_KIND,
            "boot": boot,
            "owner_user_id": RUNNER_OWNER,
            "discoveries": discoveries,
            "capabilities": detect_capabilities(),
            "auto_probe": True,
            "checkouts": collect_checkouts(),
            "usage_snapshots": usage_snapshots(backends),
        }

    def on_connected(url: str) -> None:
        global HIVE_URL
        HIVE_URL = url  # task-execution and cancel-watch clients follow the winner

    def run_task(task: dict) -> dict:
        log.info("executing %s task %s on %s", task["kind"], task["id"], task["repo"])
        result = execute(task, headers, auth)
        log.info("task %s done (error=%s)", task["id"], result.get("is_error"))
        return result

    last_update_check = time.monotonic()
    update_checked_for = ""  # chief version we already fetched for, to fetch once per skew

    def between_tasks(data: dict) -> str:
        # Self-update (dedicated service clones only): exit between tasks when
        # origin/main moved; the service wrapper pulls and respawns. Checked on
        # a timer, and immediately when the chief's version changes (a deploy
        # restarts it) so the fleet runs mixed versions for seconds, not
        # minutes. The update_available() gate matters: a chief on uncommitted
        # code (push.sh ships working trees) must not exit-loop runners that
        # can only ever update to origin/main — hence the memo.
        nonlocal last_update_check, update_checked_for
        if not SELF_UPDATE:
            return ""
        chief_version = data.get("chief_version", "")
        version_skew = chief_version and chief_version not in (RUNNER_VERSION, update_checked_for)
        timer_due = time.monotonic() - last_update_check > UPDATE_CHECK_INTERVAL_S
        if not (timer_due or version_skew):
            return ""
        if version_skew:
            update_checked_for = chief_version
        last_update_check = time.monotonic()
        if update_available(REPO_ROOT):
            return "origin/main moved — exiting so the service wrapper updates us"
        return ""

    WorkerLoop(
        WorkerConfig(
            urls=parse_urls(HIVE_URL),
            state_path=CHIEF_STATE_PATH,
            headers=headers,
            auth=auth,
        ),
        payload=payload,
        execute=run_task,
        on_connected=on_connected,
        between_tasks=between_tasks,
    ).run()


if __name__ == "__main__":
    main()
