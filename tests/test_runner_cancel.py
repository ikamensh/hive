"""The cancel watcher must outlive transient chief failures.

Regression for a live incident (2026-07-11): a transient Caddy 502 with an
empty body made `response.json()` raise JSONDecodeError inside the watcher
thread, killing it silently — the task kept running but operator cancellation
was no longer observed. The watcher's contract, pinned here: any single bad
poll (proxy 5xx, unreachable chief, garbage body) is skipped, and a later
`cancel_requested` still terminates the agent session.

Driven synchronously over httpx.MockTransport — `watch_for_cancel` returns
once it detects the cancel, so no threads are needed; if a bad poll kills the
watcher, the scripted failure's exception propagates and fails the test.
"""

from __future__ import annotations

import threading

import httpx

from hive.runner._daemon import watch_for_cancel


class FakeSession:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


def run_watcher(script: list) -> FakeSession:
    """Run watch_for_cancel over scripted poll outcomes (httpx.Response to
    return, or an exception to raise). The last entry repeats and must be the
    cancel, or the watcher would rightly poll forever."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    watcher = httpx.Client(base_url="http://chief", transport=httpx.MockTransport(handler))
    session = FakeSession()
    cancelled = threading.Event()
    watch_for_cancel(watcher, "t1", threading.Event(), cancelled, session, poll_s=0.001)
    assert cancelled.is_set()
    return session


def test_watcher_survives_proxy_502_with_empty_body():
    # The exact observed failure: Caddy answering 502 with no body.
    session = run_watcher(
        [
            httpx.Response(502, content=b""),
            httpx.Response(200, json={"cancel_requested": False}),
            httpx.Response(200, json={"cancel_requested": True}),
        ]
    )
    assert session.terminated


def test_watcher_survives_undecodable_2xx_body():
    # A misrouted proxy can answer 200 with a non-JSON body just as well.
    session = run_watcher(
        [
            httpx.Response(200, content=b"<html>gateway</html>"),
            httpx.Response(200, json={"cancel_requested": True}),
        ]
    )
    assert session.terminated


def test_watcher_survives_unreachable_chief():
    session = run_watcher(
        [
            httpx.ConnectError("chief down"),
            httpx.Response(200, json={"cancel_requested": True}),
        ]
    )
    assert session.terminated


def test_watcher_stops_without_terminating_when_task_ends():
    # execute() sets stop_watch when the task finishes; the watcher must exit
    # without touching the session.
    watcher = httpx.Client(
        base_url="http://chief",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
    )
    session = FakeSession()
    stop_watch = threading.Event()
    stop_watch.set()
    watch_for_cancel(watcher, "t1", stop_watch, threading.Event(), session, poll_s=0.001)
    assert not session.terminated
