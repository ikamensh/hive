"""Record a live Hive experiment without changing its state.

uv run python experiments/production-simulator/observe.py PROJECT --output /path/to/evidence
Writes full snapshots, a JSONL event timeline, and a continuously refreshed report.
The output directory must be empty. Ctrl-C stops observation, not Hive.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from http.client import HTTPException
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import urlopen


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class TemporaryOutage(Exception):
    """Only retry transport failures and explicitly transient HTTP statuses."""


def read_json(url: str):
    try:
        with urlopen(url, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        # Never print response bodies, URLs, or credentials in error output.
        if exc.code in (408, 429) or exc.code >= 500:
            raise TemporaryOutage(f"HTTP {exc.code}") from exc
        raise RuntimeError(f"HTTP {exc.code}; check the selected chief and access") from exc
    except (URLError, TimeoutError, ConnectionError, HTTPException) as exc:
        raise TemporaryOutage(type(exc).__name__) from exc


def material(value):
    """Ignore heartbeat/presentation clocks when detecting substantive changes."""
    if isinstance(value, dict):
        return {key: material(item) for key, item in value.items()
                if key not in {"updated_at", "last_seen", "snapshot_age_s"}}
    if isinstance(value, list):
        return [material(item) for item in value]
    return value


class Observer:
    def __init__(self, project: str, url: str, output: Path, resource_interval: float):
        if output.exists() and any(output.iterdir()):
            raise ValueError("output directory must be empty; select a new evidence directory")
        output.mkdir(parents=True, exist_ok=True)
        (output / "snapshots").mkdir()
        self.output, self.url, self.project = output, url.rstrip("/"), project
        self.project_id = ""
        self.started = time.time()
        self.resource_interval = resource_interval
        self.next_resources = 0.0
        self.last_material = None
        self.detail = None
        self.tasks: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self.sequence = 0
        self.outage_start = 0.0
        self.outage_seconds = 0.0
        self.outages = 0
        self.waiting = False
        self.event("started", project=project)

    def event(self, event: str, **fields):
        with (self.output / "timeline.jsonl").open("a") as stream:
            stream.write(json.dumps({"at": stamp(time.time()), "event": event, **fields}) + "\n")

    def snapshot(self, kind: str, payload: dict) -> str:
        self.sequence += 1
        relative = f"snapshots/{kind}-{self.sequence:06d}.json"
        (self.output / relative).write_text(json.dumps({"at": stamp(time.time()), "payload": payload}, indent=2) + "\n")
        return relative

    def poll(self):
        if not self.project_id:
            projects = read_json(f"{self.url}/api/projects")
            matches = [p for p in projects if self.project in (p["id"], p["name"])]
            if len(matches) > 1:
                raise ValueError("project name is ambiguous; use its ID")
            if not matches:
                if not self.waiting:
                    self.event("waiting_for_project")
                    self.waiting = True
                return
            self.project_id = matches[0]["id"]
            self.event("project_found", project_id=self.project_id)
        detail = read_json(f"{self.url}/api/projects/{quote(self.project_id, safe='')}")
        stable = material(detail)
        if stable != self.last_material:
            plan = detail.get("plan") or {}
            tasks = {task["id"]: task for task in [*detail.get("tasks", []), *plan.get("tasks", [])]}
            changed = [task for key, task in tasks.items() if material(self.tasks.get(key)) != material(task)]
            self.tasks.update(tasks)
            self.items.update({item["id"]: item for item in plan.get("items", [])})
            self.detail, self.last_material = detail, stable
            self.event("project_changed", snapshot=self.snapshot("project", detail),
                       state=detail["project"]["state"], reason=detail.get("state_reason", ""),
                       plan_status=plan.get("plan", {}).get("status", ""),
                       items=[{"id": item["id"], "status": item["status"],
                               "repairs": item.get("repair_attempts", 0)} for item in plan.get("items", [])],
                       tasks=[{key: task.get(key) for key in
                               ("id", "kind", "backend", "model", "status", "dispatch_reason",
                                "runner_id", "started_at", "finished_at", "retryable_interruption")}
                              for task in changed])
        if time.time() >= self.next_resources:
            capacity = {"resources": read_json(f"{self.url}/api/resources"),
                        "limits": read_json(f"{self.url}/api/show").get("limits", [])}
            self.event("capacity", snapshot=self.snapshot("capacity", capacity))
            self.next_resources = time.time() + self.resource_interval

    def sample(self):
        try:
            self.poll()
        except TemporaryOutage as exc:
            if not self.outage_start:
                self.outage_start = time.time()
                self.outages += 1
                self.event("outage", reason=str(exc))
        else:
            if self.outage_start:
                duration = time.time() - self.outage_start
                self.outage_seconds += duration
                self.outage_start = 0.0
                self.event("recovered", outage_seconds=round(duration, 3))
        self.report()

    def report(self, stopped: bool = False):
        now = time.time()
        detail = self.detail or {}
        plan = detail.get("plan") or {}
        items = list(self.items.values())
        tasks = list(self.tasks.values())
        models = sorted({(task["backend"], task.get("model") or "default") for task in tasks})
        interruptions = sum(bool(task.get("retryable_interruption")) for task in tasks)
        repairs = sum(item.get("repair_attempts", 0) for item in items)
        outage_seconds = self.outage_seconds + (now - self.outage_start if self.outage_start else 0)
        lines = [f"# Hive experiment: {self.project}", "", f"Started: {stamp(self.started)}",
                 f"{'Stopped' if stopped else 'Updated'}: {stamp(now)}",
                 f"Observed duration: {now - self.started:.1f} seconds",
                 f"Project ID: {self.project_id or 'waiting for creation'}",
                 f"Plan: {plan.get('plan', {}).get('status', 'none')}",
                 f"State: {detail.get('state_reason') or detail.get('project', {}).get('state', 'unknown')}", "",
                 f"Landed: {sum(item['status'] == 'done' for item in items)} / {len(items)} items",
                 f"Interrupted attempts marked retryable: {interruptions}",
                 f"Repair attempts: {repairs}",
                 f"Observer outages: {self.outages} ({outage_seconds:.1f} seconds)",
                 f"Reported task spend: ${sum(task.get('cost_usd', 0) for task in tasks):.6f}", "",
                 "Counts describe tasks observed, not provider-wide usage. Zero reported spend does not establish billing mode.",
                 "", "| Backend / model | Tasks | Started | Done | Failed | Agent seconds | Queue seconds |",
                 "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for backend, model in models:
            group = [task for task in tasks if (task["backend"], task.get("model") or "default") == (backend, model)]
            statuses = Counter(task["status"] for task in group)
            started = [task for task in group if task.get("started_at")]
            duration = sum(max(0, (task.get("finished_at") or now) - task["started_at"]) for task in started)
            queued = sum(max(0, task["started_at"] - task.get("created_at", task["started_at"])) for task in started)
            lines.append(f"| {backend} / {model} | {len(group)} | {len(started)} | {statuses['done']} | {statuses['failed']} | {duration:.1f} | {queued:.1f} |")
        lines += ["", "Full states and validation evidence: [snapshots](snapshots).",
                  "Chronological transitions and outages: [timeline.jsonl](timeline.jsonl).", ""]
        temporary = self.output / "report.md.tmp"
        temporary.write_text("\n".join(lines))
        temporary.replace(self.output / "report.md")


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive number of seconds")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="project ID or name; waits if not created yet")
    parser.add_argument("--output", required=True, type=Path, help="empty directory for evidence")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=positive_seconds, default=5.0)
    parser.add_argument("--resource-interval", type=positive_seconds, default=60.0)
    parser.add_argument("--once", action="store_true", help="capture one sample and exit")
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("URL must be an HTTP(S) chief address without credentials, query, or fragment")
    observer = Observer(args.project, args.url, args.output.expanduser(), args.resource_interval)
    print(f"Observing into {observer.output.resolve()}", flush=True)
    try:
        while True:
            observer.sample()
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        observer.event("stopped")
        observer.report(stopped=True)


if __name__ == "__main__":
    main()
