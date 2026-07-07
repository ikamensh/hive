"""A complete chief for `hive.worker` workers, in ~40 lines (demo helper).

Any process that answers these three endpoints can command a worker fleet:
register (hand out a worker id + advertise reachable URLs), poll (hand out one
task dict or null), result (record what came back). Used by the demos in this
directory; not part of the package.
"""

from __future__ import annotations

import threading

import uvicorn
from fastapi import FastAPI


class ToyChief:
    def __init__(self, port: int, tasks: list[dict], advertised: list[str] | None = None):
        self.port = port
        self.queue = list(tasks)
        self.results: dict[str, dict] = {}
        app = FastAPI()

        @app.post("/api/runners/register")
        def register(body: dict) -> dict:
            return {"runner_id": f"worker@{body.get('name', '?')}", "chief_urls": advertised or []}

        @app.post("/api/runners/{worker_id}/poll")
        def poll(worker_id: str) -> dict:
            return {"task": self.queue.pop(0) if self.queue else None}

        @app.post("/api/tasks/{task_id}/result")
        def result(task_id: str, body: dict) -> dict:
            self.results[task_id] = body
            return {}

        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "ToyChief":
        self._thread.start()
        while not self._server.started:
            pass
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
