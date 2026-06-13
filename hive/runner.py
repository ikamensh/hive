"""Runner daemon: registers with the control plane, long-polls for tasks,
executes them with a kodo agent in a local checkout, reports results.

Run directly: `python -m hive.runner`. Configuration via environment:
"""

from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from hive.backends import BACKEND_NAMES, PROBE_MARKER, make_session

HIVE_URL = os.environ.get("HIVE_URL", "http://localhost:8000")
HIVE_BASIC_AUTH = os.environ.get("HIVE_BASIC_AUTH", "")  # "user:pass" when behind Caddy
RUNNER_TOKEN = os.environ.get("HIVE_RUNNER_TOKEN", "dev-token")
RUNNER_NAME = os.environ.get("HIVE_RUNNER_NAME", socket.gethostname())
WORKDIR = Path(os.environ.get("HIVE_RUNNER_WORKDIR", "~/hive-work")).expanduser()
TASK_TIMEOUT_S = float(os.environ.get("HIVE_TASK_TIMEOUT_S", "3600"))
CANCEL_POLL_S = 5.0  # how often a running task checks for an operator cancel request

log = logging.getLogger("hive.runner")

EXHAUSTED_PATTERNS = re.compile(
    r"rate.?limit|quota|usage.?limit|plan.?limit|too many requests|429\b|subscription|billing",
    re.IGNORECASE,
)


def detect_backends() -> list[str]:
    from kodo.factory import available_backends

    return [name for name, ok in available_backends().items() if ok and name in BACKEND_NAMES]


def checkout(repo_url: str, branch: str = "") -> Path:
    """Fresh-ish checkout: clone once, fetch, then hard-reset to the target.

    With no branch, resets to the origin default (work that lands on main).
    With a branch, checks out that branch — existing on origin (verify/fix of
    PR-mode work) or freshly created off the default (the first PR-mode work
    task, which then pushes it)."""
    slug = repo_url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    path = WORKDIR / slug
    if path.exists():
        subprocess.run(["git", "fetch", "origin"], cwd=path, check=True, timeout=300)
    else:
        WORKDIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", repo_url, str(path)], check=True, timeout=600)
    default_head = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
        cwd=path, capture_output=True, text=True,
    ).stdout.strip() or "origin/main"
    if branch:
        on_origin = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", branch],
            cwd=path, capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        base = f"origin/{branch}" if on_origin else default_head
        if on_origin:
            subprocess.run(["git", "fetch", "origin", branch], cwd=path, check=True, timeout=120)
        subprocess.run(["git", "checkout", "-B", branch, base], cwd=path, check=True, timeout=60)
    else:
        subprocess.run(["git", "reset", "--hard", default_head], cwd=path, check=True, timeout=60)
    subprocess.run(["git", "clean", "-fd"], cwd=path, check=True, timeout=60)
    return path


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


def execute(task: dict, headers: dict, auth) -> dict:
    from kodo import log as kodo_log
    from kodo.agent import Agent

    try:
        project_dir = checkout(task["repo"], task.get("branch", ""))
    except subprocess.SubprocessError as exc:
        return {"text": f"checkout failed: {exc}", "is_error": True}

    kodo_log.init(kodo_log.RunDir.create(project_dir))  # capture a per-task JSONL trace
    session = make_session(task["backend"], task.get("model", ""))
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
            result = agent.run(task["instructions"], project_dir, agent_name=task["kind"])
    except BaseException:
        if cancelled.is_set():
            return {"text": "Task cancelled by operator.", "cancelled": True}
        raise
    finally:
        stop_watch.set()
        _upload_trace(task["id"], kodo_log.get_log_file(), headers, auth)

    if cancelled.is_set():
        return {"text": "Task cancelled by operator.", "cancelled": True}
    query = result.query
    text = result.text
    is_error = result.is_error
    if task["kind"] == "probe":
        text, is_error = validate_probe_result(project_dir, text, is_error)
    return {
        "text": text,
        "is_error": is_error,
        "cost_usd": query.cost_usd or 0.0,
        "input_tokens": query.input_tokens or 0,
        "output_tokens": query.output_tokens or 0,
        "resource_exhausted": bool(is_error and EXHAUSTED_PATTERNS.search(text)),
    }


def validate_probe_result(project_dir: Path, text: str, is_error: bool) -> tuple[str, bool]:
    diagnostics = []
    if PROBE_MARKER not in text:
        diagnostics.append(f"probe marker {PROBE_MARKER!r} was not found in the agent reply")
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

        detected = detect_backends()
        print(
            json.dumps(
                {
                    "supported": list(BACKEND_NAMES),
                    "detected": detected,
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
    headers = {"X-Hive-Token": RUNNER_TOKEN}
    auth = tuple(HIVE_BASIC_AUTH.split(":", 1)) if HIVE_BASIC_AUTH else None
    client = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=40.0, auth=auth)

    backends = detect_backends()
    if not backends:
        log.error("no supported agents found; not registering empty runner capacity")
        return
    runner_id = client.post(
        "/api/runners/register", json={"name": RUNNER_NAME, "backends": backends, "boot": True}
    ).raise_for_status().json()["runner_id"]
    log.info("registered as %s (%s) with backends %s", RUNNER_NAME, runner_id, backends)

    def heartbeat() -> None:
        # Keeps last_seen fresh while a long task blocks the main loop;
        # otherwise the control plane declares us offline and orphans the task.
        hb = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=15.0, auth=auth)
        while True:
            time.sleep(30)
            try:
                hb.post("/api/runners/register", json={"name": RUNNER_NAME, "backends": backends})
            except httpx.HTTPError:
                pass

    threading.Thread(target=heartbeat, daemon=True).start()

    while True:
        try:
            response = client.post(f"/api/runners/{runner_id}/poll")
            if response.status_code == 404:
                runner_id = client.post(
                    "/api/runners/register", json={"name": RUNNER_NAME, "backends": backends}
                ).raise_for_status().json()["runner_id"]
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
