"""The worker lifecycle: register → heartbeat → poll → execute → report.

`WorkerLoop` is the protocol client for a hive-style chief — three endpoints:

    POST {register_path}            -> {"runner_id": ..., "chief_urls": [...]}
    POST {poll_path}                -> {"task": {...} | null, ...}
    POST {result_path}              -> recorded (idempotently) by the chief

Everything task-specific is injected: `payload` builds the register body (the
worker's capabilities, whatever they are), `execute` turns a task dict into a
result dict. The loop owns the ugly parts of being a long-lived remote worker:
trying roster candidates until a chief answers, heartbeating through long
tasks, re-registering when the chief forgets us, re-resolving across the
roster after repeated errors (a relocated chief only has to be advertised),
and retrying result delivery hard — a result lost to a chief restart would
strand the task forever.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from hive.worker.roster import ChiefRoster

log = logging.getLogger("hive.worker.loop")


def _default_client(url: str, config: WorkerConfig) -> httpx.Client:
    return httpx.Client(
        base_url=url, headers=config.headers, timeout=config.poll_timeout_s, auth=config.auth
    )


@dataclass
class WorkerConfig:
    """Where the chiefs are, how to authenticate, and how patient to be."""

    urls: list[str]  # seed chief URLs, most preferred first
    state_path: Path  # roster persistence (survives restarts)
    headers: dict[str, str] = field(default_factory=dict)
    auth: tuple[str, str] | None = None  # basic auth, when the chief sits behind one
    register_path: str = "/api/runners/register"
    poll_path: str = "/api/runners/{worker_id}/poll"
    result_path: str = "/api/tasks/{task_id}/result"
    heartbeat_s: float = 30.0
    poll_timeout_s: float = 40.0
    reconnect_after_failures: int = 3  # consecutive errors before re-resolving the roster
    retry_s: float = 10.0  # pause after a transient error
    result_report_retry_s: float = 600.0  # how long to fight for result delivery
    # Pause after an empty poll. Hive's chief long-polls (the request itself
    # blocks), so 0 is right there; against a chief that answers immediately,
    # set this to avoid a busy loop.
    poll_idle_s: float = 0.0
    # Injection seam for tests (httpx.MockTransport); production uses real clients.
    client_factory: Callable[[str, "WorkerConfig"], httpx.Client] = _default_client


class WorkerLoop:
    """Drive the worker lifecycle against whichever chief answers.

    `payload(boot)` returns the register body (`boot=True` only for this
    process's first successful registration — the chief uses it to requeue work
    a dead predecessor dropped). `execute(task)` does the actual work.
    `on_connected(url)` fires whenever a chief accepts registration at `url`;
    `between_tasks(poll_data)` may return a reason string to end the loop
    gracefully (self-update, drain, ...).
    """

    def __init__(
        self,
        config: WorkerConfig,
        *,
        payload: Callable[[bool], dict],
        execute: Callable[[dict], dict],
        on_connected: Callable[[str], None] | None = None,
        between_tasks: Callable[[dict], str] | None = None,
    ) -> None:
        self.config = config
        self.payload = payload
        self.execute = execute
        self.on_connected = on_connected
        self.between_tasks = between_tasks
        self.roster = ChiefRoster(list(config.urls), config.state_path)
        self.worker_id = ""
        self.current_url = ""
        self._stop = threading.Event()
        self._heartbeat_started = False

    # -- registration ------------------------------------------------------

    def _register(self, client: httpx.Client, *, boot: bool = False) -> str:
        data = (
            client.post(self.config.register_path, json=self.payload(boot))
            .raise_for_status()
            .json()
        )
        self.roster.merge_advertised(data.get("chief_urls", []))
        return data["runner_id"]

    def _connect(self, *, boot: bool = False) -> httpx.Client:
        """Try roster candidates in order until a chief accepts registration.
        The first responder is the right one — a chief deployment guarantees at
        most one live chief per fleet (hive does this with a leader lease)."""
        last_error: Exception | None = None
        for url in self.roster.candidates():
            candidate = self.config.client_factory(url, self.config)
            try:
                worker_id = self._register(candidate, boot=boot)
            except (httpx.HTTPError, OSError) as exc:
                candidate.close()
                last_error = exc
                continue
            self.worker_id = worker_id
            self.current_url = url
            self.roster.mark_success(url)
            if self.on_connected is not None:
                self.on_connected(url)
            log.info("registered as worker %s at %s", worker_id, url)
            return candidate
        raise ConnectionError(f"no chief reachable among {self.roster.candidates()}: {last_error}")

    def _heartbeat(self) -> None:
        # Keeps the worker visibly alive while a long task blocks the main
        # loop; a fresh client each beat follows current_url when the chief moves.
        while not self._stop.wait(self.config.heartbeat_s):
            try:
                with self.config.client_factory(self.current_url, self.config) as client:
                    self._register(client)
            except (httpx.HTTPError, OSError):
                pass

    # -- results -----------------------------------------------------------

    def report_result(self, task_id: str, result: dict) -> None:
        """Deliver a finished task's result, retrying transient failures hard.

        The chief records results idempotently (a non-running task ignores late
        posts), so retrying is always safe. Client errors mean the chief made a
        decision about this task (cancelled/deleted) — no point retrying those.
        """
        deadline = time.monotonic() + self.config.result_report_retry_s
        delay = 2.0
        path = self.config.result_path.format(task_id=task_id)
        while True:
            try:
                with self.config.client_factory(self.current_url, self.config) as client:
                    client.post(path, json=result).raise_for_status()
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    log.error("chief rejected result for task %s: %s", task_id, exc)
                    return
                failure: Exception = exc
            except (httpx.HTTPError, OSError) as exc:
                failure = exc
            if time.monotonic() > deadline:
                log.error("giving up reporting result for task %s: %s", task_id, failure)
                return
            log.warning(
                "result report for task %s failed (%s) — retrying in %.0fs",
                task_id,
                failure,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30.0)

    # -- the loop ----------------------------------------------------------

    def stop(self) -> None:
        """Ask a running loop (typically in another thread) to wind down."""
        self._stop.set()

    def run(self, *, max_tasks: int | None = None) -> str:
        """Work until `between_tasks` names an exit reason, `max_tasks` tasks
        finished, or `stop()` was called. Returns the exit reason ("" for the
        latter two)."""
        client: httpx.Client | None = None
        while client is None and not self._stop.is_set():
            try:
                client = self._connect(boot=True)
            except (ConnectionError, OSError) as exc:
                log.warning("%s — retrying in %.0fs", exc, self.config.retry_s)
                self._stop.wait(self.config.retry_s)
        if client is None:
            return ""

        if not self._heartbeat_started:  # one thread even if run() is called twice
            self._heartbeat_started = True
            threading.Thread(target=self._heartbeat, daemon=True).start()

        failures = 0
        done = 0
        try:
            while not self._stop.is_set():
                try:
                    if client is None:
                        client = self._connect()
                        failures = 0
                    response = client.post(
                        self.config.poll_path.format(worker_id=self.worker_id)
                    )
                    if response.status_code == 404:  # chief forgot us: fresh identity
                        self.worker_id = self._register(client)
                        continue
                    failures = 0
                    data = response.raise_for_status().json()
                    if self.between_tasks is not None and (reason := self.between_tasks(data)):
                        log.info("exiting worker loop: %s", reason)
                        return reason
                    task = data.get("task")
                    if not task:
                        if self.config.poll_idle_s:
                            self._stop.wait(self.config.poll_idle_s)
                        continue
                    log.info("executing task %s", task.get("id"))
                    result = self.execute(task)
                    self.report_result(task["id"], result)
                    done += 1
                    if max_tasks is not None and done >= max_tasks:
                        return ""
                except (httpx.HTTPError, OSError) as exc:
                    failures += 1
                    log.warning("transient error: %s — retrying in %.0fs", exc, self.config.retry_s)
                    if client is not None and failures >= self.config.reconnect_after_failures:
                        client.close()
                        client = None  # next iteration re-resolves across the roster
                    self._stop.wait(self.config.retry_s)
        finally:
            self._stop.set()  # winds down the heartbeat thread
            if client is not None:
                client.close()
        return ""
