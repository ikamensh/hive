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
import time

from hive.config_file import (
    CONFIG_KEYS,
    config_path,
    load_stored_config,
    save_stored_config,
)

UVICORN_GRACEFUL_SHUTDOWN_S = 6


def _is_secret(key: str) -> bool:
    return key.endswith("_TOKEN") or key.endswith("_API_KEY") or key.endswith("_SECRET")


def _mask(key: str, value: str) -> str:
    if not _is_secret(key) or not value:
        return value
    return f"…{value[-4:]}" if len(value) > 4 else "****"


def _gh_token(preferred_user: str = "") -> str:
    from hive.github_repos import gh_token_for

    return gh_token_for(preferred_user)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hive", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="launch the local control plane (auto-detects tokens)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="auto-reload on code changes")

    p = sub.add_parser("config", help="manage hive's own stored tokens/settings")
    csub = p.add_subparsers(dest="config_command", required=True)
    csub.add_parser("show", help="show stored config (secrets masked)")
    cset = csub.add_parser("set", help="store a token/setting (overrides ambient env on `run`)")
    cset.add_argument("key", choices=sorted(CONFIG_KEYS))
    cset.add_argument("value")
    cunset = csub.add_parser("unset", help="remove a stored key")
    cunset.add_argument("key", choices=sorted(CONFIG_KEYS))
    cimp = csub.add_parser("import", help="seed stored config from `gh` + current environment")
    cimp.add_argument("--force", action="store_true", help="overwrite keys already stored")

    p = sub.add_parser("projects", help="list projects")

    p = sub.add_parser("create", help="create a project (name only; configure in project view)")
    p.add_argument("name")

    p = sub.add_parser("start", help="wake planning after approved project intake")
    p.add_argument("project_id")
    p.add_argument("--mission", default="", help=argparse.SUPPRESS)
    p.add_argument("--iteration-goal", default="", help=argparse.SUPPRESS)

    p = sub.add_parser("repo-create", help="create a private greenfield repo for a project")
    p.add_argument("project_id")
    p.add_argument("--name", default="")
    p.add_argument("--public", action="store_true")

    p = sub.add_parser("intake-start", help="start the project intake scout")
    p.add_argument("project_id")

    p = sub.add_parser("intake-send", help="send an intake answer or correction")
    p.add_argument("conversation_id")
    p.add_argument("message")

    p = sub.add_parser("intake-proceed", help="tell intake to proceed with current assumptions")
    p.add_argument("conversation_id")

    p = sub.add_parser("intake-approve", help="approve the latest intake brief and finalize specs")
    p.add_argument("conversation_id")

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
    p.add_argument("--spec-repo", help="spec home git URL")

    p = sub.add_parser("scan", help="scan the project's open GitHub issues and queue fixes")
    p.add_argument("project_id")

    p = sub.add_parser("preflight", help="check issue-solving preconditions (token, perms, runner push/gh auth)")
    p.add_argument("project_id")

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

    p = sub.add_parser("trace", help="print a task's raw kodo JSONL run trace")
    p.add_argument("task_id")

    sub.add_parser("agents", help="list locally detected supported agent backends")
    sub.add_parser("resources", help="runners and backend resources")
    p = sub.add_parser("probe", help="probe one registered backend resource")
    p.add_argument("resource_id")

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


def prepare_run_env(env: dict[str, str], stored: dict[str, str]) -> list[str]:
    """Resolve the tokens/settings the control plane will run with, mutating
    `env`, and return human-readable lines (with provenance) describing them.

    Precedence, highest first: hive's own `stored` config, then ambient env,
    then autodetection (`gh auth token`). Stored config intentionally *overrides*
    ambient env so a user can give hive separate keys — e.g. to bill/track its
    cost on a different account — while autodetected tokens are just the starting
    point you seed that store from (`hive config import`)."""
    stored = {k: v for k, v in stored.items() if v}
    for key, value in stored.items():
        env[key] = value

    notes: list[str] = []
    gh_autodetected = False
    preferred_gh = env.get("HIVE_ALLOWED_GITHUB_USERS", "ikamensh").split(",")[0].strip()
    if not env.get("HIVE_GH_TOKEN"):
        if token := _gh_token(preferred_gh):
            env["HIVE_GH_TOKEN"] = token
            gh_autodetected = True

    def src(key: str) -> str:
        return "stored config" if key in stored else "environment"

    if gh_autodetected:
        notes.append(f"github: token from `gh auth token -u {preferred_gh}`")
    elif env.get("HIVE_GH_TOKEN"):
        notes.append(f"github: HIVE_GH_TOKEN from {src('HIVE_GH_TOKEN')}")
    else:
        notes.append("github: no token (`gh auth login` or `hive config set HIVE_GH_TOKEN …`)")

    provider = env.get("HIVE_ORCH_PROVIDER", "auto")
    if env.get("OPENAI_API_KEY"):
        notes.append(f"orchestrator: OPENAI_API_KEY from {src('OPENAI_API_KEY')} (provider={provider})")
    elif env.get("GEMINI_API_KEY"):
        notes.append(f"orchestrator: GEMINI_API_KEY from {src('GEMINI_API_KEY')} (provider={provider})")
    else:
        notes.append("orchestrator: NO API key — `hive config set OPENAI_API_KEY …` or export it")

    if env.get("HIVE_GCP_PROJECT"):
        notes.append(
            f"store: Firestore ({env['HIVE_GCP_PROJECT']}, from {src('HIVE_GCP_PROJECT')})"
        )
    else:
        data_dir = env.get("HIVE_DATA_DIR", "/tmp/hive-data")
        notes.append(
            f"store: local files ({data_dir}/store; set HIVE_GCP_PROJECT for Firestore)"
        )

    auth_mode = env.get("HIVE_AUTH_MODE", "dev")
    if auth_mode == "github":
        allowed = env.get("HIVE_ALLOWED_GITHUB_USERS", "ikamensh")
        notes.append(f"auth: GitHub OAuth allowlist ({allowed})")
    else:
        notes.append("auth: dev mode (local/test only)")

    runner_mode = (
        "enabled"
        if env.get("HIVE_AUTOSTART_RUNNER", "").lower() in {"1", "true", "yes", "on"}
        else "disabled"
    )
    notes.append(f"local runner autostart: {runner_mode}")

    return notes


def detect_config(env: dict[str, str]) -> dict[str, str]:
    """Tokens/settings discoverable on this machine, as a seed for the store:
    the `gh` token plus any recognized hive vars already in the environment."""
    found = {key: env[key] for key in CONFIG_KEYS if env.get(key)}
    if "HIVE_GH_TOKEN" not in found and (token := _gh_token(env.get("HIVE_ALLOWED_GITHUB_USERS", "ikamensh").split(",")[0].strip())):
        found["HIVE_GH_TOKEN"] = token
    return found


def _run_config(args: argparse.Namespace) -> None:
    path = config_path()
    stored = load_stored_config(path)
    action = args.config_command
    if action == "show":
        print(json.dumps({k: _mask(k, v) for k, v in stored.items()}, indent=2))
    elif action == "set":
        stored[args.key] = args.value
        save_stored_config(stored, path)
        print(f"stored {args.key} → {path}")
    elif action == "unset":
        stored.pop(args.key, None)
        save_stored_config(stored, path)
        print(f"removed {args.key} from {path}")
    elif action == "import":
        added = {
            k: v for k, v in detect_config(os.environ).items()
            if args.force or k not in stored
        }
        stored.update(added)
        save_stored_config(stored, path)
        print(json.dumps(
            {"imported": {k: _mask(k, v) for k, v in added.items()}, "path": str(path)},
            indent=2,
        ))
    else:
        raise AssertionError(f"unhandled config command {action}")


def _run_control_plane(args: argparse.Namespace) -> None:
    import uvicorn

    from hive.local_runner import local_control_plane_url

    for line in prepare_run_env(os.environ, load_stored_config()):
        print(f"  {line}")
    os.environ.setdefault("HIVE_PUBLIC_URL", local_control_plane_url(args.host, args.port))
    print(f"hive control plane → http://{args.host}:{args.port}\n")
    uvicorn.run(
        "hive.api:production_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        timeout_graceful_shutdown=UVICORN_GRACEFUL_SHUTDOWN_S,
    )


def run(args: argparse.Namespace, client) -> dict | list:
    """Execute one command against an httpx-compatible client and return the
    response payload. Non-2xx responses raise (clear failure over silence)."""
    c = args.command
    if c == "projects":
        r = client.get("/api/projects")
    elif c == "create":
        r = client.post("/api/projects", json={"name": args.name})
    elif c == "start":
        r = client.post(f"/api/projects/{args.project_id}/start", json={
            "mission": args.mission,
            "iteration_goal": args.iteration_goal,
        })
    elif c == "repo-create":
        r = client.post(f"/api/projects/{args.project_id}/repo", json={
            "name": args.name,
            "private": not args.public,
        })
    elif c == "intake-start":
        r = client.post(f"/api/projects/{args.project_id}/intake/start")
    elif c == "intake-send":
        r = client.post(f"/api/conversations/{args.conversation_id}/message", json={
            "action": "message",
            "message": args.message,
        })
    elif c == "intake-proceed":
        r = client.post(f"/api/conversations/{args.conversation_id}/message", json={
            "action": "proceed",
        })
    elif c == "intake-approve":
        r = client.post(f"/api/conversations/{args.conversation_id}/message", json={
            "action": "approve",
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
        if args.spec_repo is not None:
            body["spec_repo"] = args.spec_repo
        r = client.patch(f"/api/projects/{args.project_id}", json=body)
    elif c == "scan":
        r = client.post(f"/api/projects/{args.project_id}/scan-issues")
    elif c == "preflight":
        data = client.post(f"/api/projects/{args.project_id}/issues-preflight").raise_for_status().json()
        tid = data.get("runner_check_task")
        if tid:  # poll the runner self-check to completion (dispatched by the supervisor loop)
            for _ in range(60):
                task = client.get(f"/api/tasks/{tid}").raise_for_status().json()
                if task["status"] in ("done", "failed", "cancelled"):
                    data["runner_check"] = {"status": task["status"], "result": task.get("result_text", "")}
                    break
                time.sleep(2)
        return data
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
    elif c == "agents":
        from hive.backends import BACKEND_NAMES
        from hive.runner import discovery_payload

        detected, discoveries = discovery_payload()
        return {
            "supported": list(BACKEND_NAMES),
            "detected": detected,
            "discoveries": discoveries,
            "message": (
                "supported agents detected"
                if detected
                else "no supported agents found; install or log in to claude, cursor, codex, or gemini-cli"
            ),
        }
    elif c == "resources":
        r = client.get("/api/resources")
    elif c == "probe":
        r = client.post(f"/api/resources/{args.resource_id}/probe")
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
    if args.command == "run":
        _run_control_plane(args)
        return
    if args.command == "config":
        _run_config(args)
        return
    auth = os.environ.get("HIVE_BASIC_AUTH", "")
    client = httpx.Client(
        base_url=os.environ.get("HIVE_URL", "http://localhost:8000"),
        auth=tuple(auth.split(":", 1)) if auth else None,
        timeout=30.0,
    )
    if args.command == "trace":
        # Raw JSONL, not JSON-wrapped, so it pipes into kodo's viewer / jq.
        print(client.get(f"/api/tasks/{args.task_id}/trace").raise_for_status().text)
        return
    print(json.dumps(run(args, client), indent=2))


if __name__ == "__main__":
    main()
