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
import time
from pathlib import Path

import httpx

HIVE_URL = os.environ.get("HIVE_URL", "http://localhost:8000")
RUNNER_TOKEN = os.environ.get("HIVE_RUNNER_TOKEN", "dev-token")
RUNNER_NAME = os.environ.get("HIVE_RUNNER_NAME", socket.gethostname())
WORKDIR = Path(os.environ.get("HIVE_RUNNER_WORKDIR", "~/hive-work")).expanduser()
TASK_TIMEOUT_S = float(os.environ.get("HIVE_TASK_TIMEOUT_S", "3600"))

log = logging.getLogger("hive.runner")

EXHAUSTED_PATTERNS = re.compile(
    r"rate.?limit|quota|usage.?limit|plan.?limit|too many requests|429\b|subscription|billing",
    re.IGNORECASE,
)


def detect_backends() -> list[str]:
    from kodo.factory import available_backends

    ours = ("claude", "cursor", "codex", "gemini-cli")
    return [name for name, ok in available_backends().items() if ok and name in ours]


def make_session(backend: str, model: str):
    if backend == "claude":
        from kodo.sessions.claude import ClaudeSession

        return ClaudeSession(model=model) if model else ClaudeSession()
    if backend == "cursor":
        from kodo.sessions.cursor import CursorSession

        return CursorSession(model=model) if model else CursorSession()
    if backend == "codex":
        from kodo.sessions.codex import CodexSession

        return CodexSession(model=model, sandbox="danger-full-access") if model else CodexSession(sandbox="danger-full-access")
    if backend == "gemini-cli":
        from kodo.sessions.gemini_cli import GeminiCliSession

        return GeminiCliSession(model=model) if model else GeminiCliSession()
    raise ValueError(f"unknown backend {backend}")


def checkout(repo_url: str) -> Path:
    """Fresh-ish checkout: clone once, then fetch + hard-reset to origin default."""
    slug = repo_url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    path = WORKDIR / slug
    if path.exists():
        subprocess.run(["git", "fetch", "origin"], cwd=path, check=True, timeout=300)
        head = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
            cwd=path, capture_output=True, text=True,
        ).stdout.strip() or "origin/main"
        subprocess.run(["git", "reset", "--hard", head], cwd=path, check=True, timeout=60)
        subprocess.run(["git", "clean", "-fd"], cwd=path, check=True, timeout=60)
    else:
        WORKDIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", repo_url, str(path)], check=True, timeout=600)
    return path


def execute(task: dict) -> dict:
    from kodo.agent import Agent

    try:
        project_dir = checkout(task["repo"])
    except subprocess.SubprocessError as exc:
        return {"text": f"checkout failed: {exc}", "is_error": True}

    session = make_session(task["backend"], task.get("model", ""))
    with Agent(session, max_turns=100, timeout_s=TASK_TIMEOUT_S) as agent:
        result = agent.run(task["instructions"], project_dir, agent_name=task["kind"])
    query = result.query
    return {
        "text": result.text,
        "is_error": result.is_error,
        "cost_usd": query.cost_usd or 0.0,
        "input_tokens": query.input_tokens or 0,
        "output_tokens": query.output_tokens or 0,
        "resource_exhausted": bool(result.is_error and EXHAUSTED_PATTERNS.search(result.text)),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    headers = {"X-Hive-Token": RUNNER_TOKEN}
    client = httpx.Client(base_url=HIVE_URL, headers=headers, timeout=40.0)

    backends = detect_backends()
    runner_id = client.post(
        "/api/runners/register", json={"name": RUNNER_NAME, "backends": backends}
    ).raise_for_status().json()["runner_id"]
    log.info("registered as %s (%s) with backends %s", RUNNER_NAME, runner_id, backends)

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
            result = execute(task)
            log.info("task %s done (error=%s)", task["id"], result.get("is_error"))
            client.post(f"/api/tasks/{task['id']}/result", json=result)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("transient error: %s — retrying in 10s", exc)
            time.sleep(10)


if __name__ == "__main__":
    main()
