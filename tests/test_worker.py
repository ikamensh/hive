"""hive.worker loop properties, driven over httpx.MockTransport (no sockets).

The WorkerLoop contract: register with whichever roster candidate answers,
learn advertised chief URLs, execute polled tasks and deliver results with
hard retries, survive a chief that forgets the worker (404 -> re-register),
and exit cleanly when `between_tasks` names a reason. These are the behaviors
the fleet relies on across chief deploys/relocations, pinned here against the
public package surface.
"""

from __future__ import annotations

import json

import httpx

import hive.worker.loop as loop_mod
from hive.worker import WorkerConfig, WorkerLoop


class FakeChief:
    """The three protocol endpoints, in memory."""

    def __init__(self, name="chief", advertised=None, tasks=None):
        self.name = name
        self.advertised = advertised or []
        self.tasks = list(tasks or [])
        self.registers: list[dict] = []
        self.results: list[tuple[str, dict]] = []
        self.polls = 0
        self.forget_worker_once = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/runners/register":
            body = json.loads(request.content)
            self.registers.append(body)
            return httpx.Response(
                200, json={"runner_id": f"{self.name}-w1", "chief_urls": self.advertised}
            )
        if path.endswith("/poll"):
            self.polls += 1
            if self.forget_worker_once:
                self.forget_worker_once = False
                return httpx.Response(404)
            task = self.tasks.pop(0) if self.tasks else None
            return httpx.Response(200, json={"task": task, "chief_version": "v1"})
        if path.startswith("/api/tasks/") and path.endswith("/result"):
            task_id = path.split("/")[3]
            self.results.append((task_id, json.loads(request.content)))
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected path {path}")


def make_loop(tmp_path, chiefs: dict[str, FakeChief], urls, **kwargs) -> WorkerLoop:
    def client_factory(url: str, config: WorkerConfig) -> httpx.Client:
        chief = chiefs.get(url)
        if chief is None:  # a dead address: refuse every request
            transport = httpx.MockTransport(
                lambda req: (_ for _ in ()).throw(httpx.ConnectError("down"))
            )
        else:
            transport = httpx.MockTransport(chief.handler)
        return httpx.Client(base_url=url, transport=transport)

    config = WorkerConfig(
        urls=urls,
        state_path=tmp_path / "chiefs.json",
        retry_s=0.0,
        heartbeat_s=3600.0,  # keep the heartbeat thread quiet during tests
        client_factory=client_factory,
    )
    executed = []

    def execute(task):
        executed.append(task)
        return {"text": f"did {task['id']}"}

    loop = WorkerLoop(
        config, payload=lambda boot: {"name": "w", "boot": boot}, execute=execute, **kwargs
    )
    loop.executed = executed  # test-visible
    return loop


def test_loop_executes_tasks_and_reports_results(tmp_path):
    chief = FakeChief(tasks=[{"id": "t1"}, {"id": "t2"}])
    loop = make_loop(tmp_path, {"http://a": chief}, ["http://a"])

    loop.run(max_tasks=2)

    assert [t["id"] for t in loop.executed] == ["t1", "t2"]
    assert [(tid, r["text"]) for tid, r in chief.results] == [("t1", "did t1"), ("t2", "did t2")]
    assert chief.registers[0]["boot"] is True  # first registration announces a reboot


def test_loop_fails_over_to_advertised_chief_and_prefers_it(tmp_path):
    """A worker seeded only with a dead primary finds the successor through the
    roster it learned earlier — the chief-relocation story."""
    old = FakeChief(name="old", advertised=["http://b"], tasks=[{"id": "t1"}])
    new = FakeChief(name="new", tasks=[{"id": "t2"}])

    loop = make_loop(tmp_path, {"http://a": old, "http://b": new}, ["http://a"])
    loop.run(max_tasks=1)
    assert loop.current_url == "http://a"

    # Same state dir, but the old chief is gone: only the learned URL answers.
    loop2 = make_loop(tmp_path, {"http://b": new}, ["http://a"])
    loop2.run(max_tasks=1)
    assert loop2.current_url == "http://b"
    assert [tid for tid, _ in new.results] == ["t2"]

    # Success is remembered: the survivor is now the first candidate tried.
    assert loop2.roster.candidates()[0] == "http://b"


def test_loop_reregisters_when_chief_forgets_worker(tmp_path):
    chief = FakeChief(tasks=[{"id": "t1"}])
    chief.forget_worker_once = True
    loop = make_loop(tmp_path, {"http://a": chief}, ["http://a"])

    loop.run(max_tasks=1)

    assert len(chief.registers) == 2  # boot register + 404-triggered re-register
    assert [tid for tid, _ in chief.results] == ["t1"]


def test_between_tasks_reason_ends_loop_before_taking_work(tmp_path):
    chief = FakeChief(tasks=[{"id": "t1"}])
    loop = make_loop(
        tmp_path,
        {"http://a": chief},
        ["http://a"],
        between_tasks=lambda data: "self-update" if data.get("chief_version") == "v1" else "",
    )

    reason = loop.run()

    assert reason == "self-update"
    assert loop.executed == []  # exited between tasks, never took the queued one


def test_before_poll_reason_ends_loop_without_polling(tmp_path):
    """A pause present from the start means the worker never asks for work:
    the queued task stays with the chief for another worker."""
    chief = FakeChief(tasks=[{"id": "t1"}])
    loop = make_loop(
        tmp_path, {"http://a": chief}, ["http://a"], before_poll=lambda: "paused by operator"
    )

    reason = loop.run()

    assert reason == "paused by operator"
    assert chief.polls == 0
    assert loop.executed == []
    assert chief.tasks == [{"id": "t1"}]  # still queued, never claimed by us


def test_before_poll_drain_lets_an_assigned_task_finish(tmp_path):
    """The drain contract: a pause that lands while a task is in flight takes
    effect only after that task executed *and reported* — before_poll exits
    before the next poll, so pausing never abandons assigned work (which is
    exactly what between_tasks, firing after the poll, cannot guarantee)."""
    chief = FakeChief(tasks=[{"id": "t1"}])
    answers = ["", "paused by operator"]  # the flag appears during the first cycle
    loop = make_loop(
        tmp_path,
        {"http://a": chief},
        ["http://a"],
        before_poll=lambda: answers.pop(0) if answers else "paused by operator",
    )

    reason = loop.run()

    assert reason == "paused by operator"
    assert [t["id"] for t in loop.executed] == ["t1"]
    assert [tid for tid, _ in chief.results] == [("t1")]
    assert chief.polls == 1  # the drain exit happened before a second poll


def test_on_registered_sees_every_register_response(tmp_path):
    """Chief-owned facts ride the register channel: the hook fires with the
    full response body on the boot registration (workers mirror these facts
    locally, e.g. which backends the chief considers usable)."""
    chief = FakeChief(tasks=[{"id": "t1"}])
    seen: list[dict] = []
    loop = make_loop(tmp_path, {"http://a": chief}, ["http://a"], on_registered=seen.append)

    loop.run(max_tasks=1)

    assert seen and all(d["runner_id"] == "chief-w1" for d in seen)


def test_report_result_retries_transient_chief_outage(tmp_path, monkeypatch):
    """A finished task's result survives a chief restart window: transient
    transport/5xx failures are retried (the chief records results
    idempotently), while a 4xx means the chief decided about this task and
    retrying is pointless."""
    attempts = []

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) < 3:
            raise httpx.ConnectError("chief restarting")
        return httpx.Response(200, json={})

    loop = WorkerLoop(
        WorkerConfig(
            urls=["http://a"],
            state_path=tmp_path / "chiefs.json",
            client_factory=lambda url, config: httpx.Client(
                base_url=url, transport=httpx.MockTransport(flaky)
            ),
        ),
        payload=lambda boot: {},
        execute=lambda task: {},
    )
    loop.current_url = "http://a"
    monkeypatch.setattr(loop_mod.time, "sleep", lambda s: None)

    loop.report_result("t-1", {"text": "done"})

    assert attempts == ["/api/tasks/t-1/result"] * 3


def test_report_result_gives_up_on_client_error(tmp_path):
    attempts = []

    def rejecting(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        return httpx.Response(404)

    loop = WorkerLoop(
        WorkerConfig(
            urls=["http://a"],
            state_path=tmp_path / "chiefs.json",
            client_factory=lambda url, config: httpx.Client(
                base_url=url, transport=httpx.MockTransport(rejecting)
            ),
        ),
        payload=lambda boot: {},
        execute=lambda task: {},
    )
    loop.current_url = "http://a"

    loop.report_result("t-2", {"text": "done"})

    assert attempts == ["/api/tasks/t-2/result"]
