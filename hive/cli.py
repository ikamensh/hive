"""CLI for the hive web API — full parity with the UI, JSON in/out.

Built for agents as much as humans: every command prints the API response as
JSON, so `hive projects | jq ...` and scripted tests work the same way the
web UI does. Talks to HIVE_URL (default http://localhost:8000); set
HIVE_BASIC_AUTH="user:pass" when the endpoint sits behind Caddy.

Run as `python -m hive.cli <command>` or the `hive` console script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hive", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("projects", help="list projects")

    p = sub.add_parser("create", help="create a project")
    p.add_argument("name")
    p.add_argument("spec_repo")
    p.add_argument("--member-repos", default="", help="comma-separated git URLs")
    p.add_argument("--mode", default="build")
    p.add_argument("--autonomy", default="direct_push")
    p.add_argument("--guess-propensity", default="sometimes")

    p = sub.add_parser("show", help="project detail: workstreams, tasks, questions")
    p.add_argument("project_id")

    p = sub.add_parser("set", help="patch project settings")
    p.add_argument("project_id")
    p.add_argument("--mode")
    p.add_argument("--autonomy")
    p.add_argument("--guess-propensity")
    p.add_argument("--prod-deploys", choices=["true", "false"])
    p.add_argument("--paused", choices=["true", "false"])
    p.add_argument("--daily-budget", type=float, help="daily spend cap in USD (0 = no cap)")
    p.add_argument("--member-repos", help="comma-separated git URLs (replaces the list)")

    p = sub.add_parser("iterate", help="start the next iteration with a note")
    p.add_argument("project_id")
    p.add_argument("note")

    p = sub.add_parser("answer", help="answer an open question")
    p.add_argument("question_id")
    p.add_argument("answer")

    p = sub.add_parser("dismiss", help="dismiss an open question without answering")
    p.add_argument("question_id")

    p = sub.add_parser("feedback", help="leave feedback on a task/workstream")
    p.add_argument("project_id")
    p.add_argument("target_id")
    p.add_argument("verdict")
    p.add_argument("--comment", default="")

    p = sub.add_parser("task", help="show one task (full instructions/result)")
    p.add_argument("task_id")

    p = sub.add_parser("cancel", help="cancel a task (dequeue if pending, stop if running)")
    p.add_argument("task_id")

    sub.add_parser("resources", help="runners and backend resources")

    sub.add_parser("subs", help="list subscriptions")
    p = sub.add_parser("sub-add", help="add a subscription")
    p.add_argument("provider")
    p.add_argument("--plan", default="")
    p.add_argument("--notes", default="")
    p = sub.add_parser("sub-rm", help="delete a subscription")
    p.add_argument("sub_id")

    sub.add_parser("todos", help="list human todos")
    p = sub.add_parser("todo-add", help="file a human todo")
    p.add_argument("title")
    p.add_argument("--instructions", default="")
    p.add_argument("--project-id", default="", help="empty = org-wide")
    p = sub.add_parser("todo-done", help="mark a human todo done")
    p.add_argument("task_id")

    sub.add_parser("org-context", help="print org context")
    p = sub.add_parser("org-context-set", help="set org context (from arg or stdin)")
    p.add_argument("text", nargs="?", help="omit to read from stdin")

    return parser


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def run(args: argparse.Namespace, client) -> dict | list:
    """Execute one command against an httpx-compatible client and return the
    response payload. Non-2xx responses raise (clear failure over silence)."""
    c = args.command
    if c == "projects":
        r = client.get("/api/projects")
    elif c == "create":
        r = client.post("/api/projects", json={
            "name": args.name, "spec_repo": args.spec_repo,
            "member_repos": _csv(args.member_repos), "mode": args.mode,
            "autonomy": args.autonomy, "guess_propensity": args.guess_propensity,
        })
    elif c == "show":
        r = client.get(f"/api/projects/{args.project_id}")
    elif c == "set":
        body = {k: v for k, v in {
            "mode": args.mode, "autonomy": args.autonomy,
            "guess_propensity": args.guess_propensity,
        }.items() if v is not None}
        for flag in ("prod_deploys", "paused"):
            if (v := getattr(args, flag)) is not None:
                body[flag] = v == "true"
        if args.daily_budget is not None:
            body["daily_budget_usd"] = args.daily_budget
        if args.member_repos is not None:
            body["member_repos"] = _csv(args.member_repos)
        r = client.patch(f"/api/projects/{args.project_id}", json=body)
    elif c == "iterate":
        r = client.patch(f"/api/projects/{args.project_id}",
                         json={"new_iteration_note": args.note})
    elif c == "answer":
        r = client.post(f"/api/questions/{args.question_id}/answer",
                        json={"answer": args.answer})
    elif c == "dismiss":
        r = client.post(f"/api/questions/{args.question_id}/dismiss")
    elif c == "feedback":
        r = client.post("/api/feedback", json={
            "project_id": args.project_id, "target_id": args.target_id,
            "verdict": args.verdict, "comment": args.comment,
        })
    elif c == "task":
        r = client.get(f"/api/tasks/{args.task_id}")
    elif c == "cancel":
        r = client.post(f"/api/tasks/{args.task_id}/cancel")
    elif c == "resources":
        r = client.get("/api/resources")
    elif c == "subs":
        r = client.get("/api/subscriptions")
    elif c == "sub-add":
        r = client.post("/api/subscriptions", json={
            "provider": args.provider, "plan": args.plan, "notes": args.notes,
        })
    elif c == "sub-rm":
        r = client.delete(f"/api/subscriptions/{args.sub_id}")
    elif c == "todos":
        r = client.get("/api/human-tasks")
    elif c == "todo-add":
        r = client.post("/api/human-tasks", json={
            "title": args.title, "instructions": args.instructions,
            "project_id": args.project_id,
        })
    elif c == "todo-done":
        r = client.post(f"/api/human-tasks/{args.task_id}/done")
    elif c == "org-context":
        r = client.get("/api/org-context")
    elif c == "org-context-set":
        text = args.text if args.text is not None else sys.stdin.read()
        r = client.put("/api/org-context", json={"text": text})
    else:
        raise AssertionError(f"unhandled command {c}")
    r.raise_for_status()
    return r.json()


def main(argv: list[str] | None = None) -> None:
    import httpx

    args = build_parser().parse_args(argv)
    auth = os.environ.get("HIVE_BASIC_AUTH", "")
    client = httpx.Client(
        base_url=os.environ.get("HIVE_URL", "http://localhost:8000"),
        auth=tuple(auth.split(":", 1)) if auth else None,
        timeout=30.0,
    )
    print(json.dumps(run(args, client), indent=2))


if __name__ == "__main__":
    main()
