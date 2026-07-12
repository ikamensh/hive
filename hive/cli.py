"""CLI for the hive web API — full parity with the UI, JSON in/out.

Built for agents as much as humans: every command prints the API response as
JSON, so `hive projects | jq ...` and scripted tests work the same way the
web UI does. The one human-first exception is `hive show`, which renders a
readable summary by default (`--json` restores the raw payload).

Where it sends commands and how it authenticates is a *client target*:
HIVE_URL, HIVE_BASIC_AUTH="user:pass" for a chief behind basic auth (Caddy),
and HIVE_TOKEN for app-level (github) auth. Each can be a one-off env var or
persisted with `hive config set …` (env wins, so ad-hoc targeting overrides
the saved default). With no HIVE_URL configured the CLI discovers the chief:
localhost:8000 first (the `hive run` dev loop), then the chief this machine's
installed runner reports to (`~/.config/hive/runner.env`). `hive whoami`
resolves the target and reports the authenticated identity.

Run as `python -m hive.cli <command>` or the `hive` console script.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from hive.config.file import (
    CONFIG_KEYS,
    config_path,
    load_stored_config,
    save_stored_config,
)
from hive.version import get_version, version_payload

UVICORN_GRACEFUL_SHUTDOWN_S = 6

# Keys that describe where the CLI *sends* commands and how it authenticates,
# as opposed to the secrets a chief *runs* with. They are persisted in
# the same store but never injected into a `hive run` server process.
CLIENT_KEYS = ("HIVE_URL", "HIVE_BASIC_AUTH", "HIVE_TOKEN")

DEFAULT_HIVE_URL = "http://localhost:8000"


class Target(NamedTuple):
    """A resolved chief connection: where + how to authenticate."""

    base_url: str
    auth: tuple[str, str] | None  # basic auth (Caddy perimeter)
    token: str  # bearer token (app-level github auth)


def _basic_auth(value: str) -> tuple[str, str] | None:
    return tuple(value.split(":", 1)) if value else None  # type: ignore[return-value]


def runner_env_target() -> Target | None:
    """The chief this machine's installed runner reports to.

    Runner installs materialize their credentials file (Mac launchd:
    `~/.config/hive/runner.env`) with the chief URL and its perimeter
    credentials, so a machine that already executes hive tasks can drive that
    same chief with zero CLI configuration."""
    path = Path(
        os.environ.get("HIVE_RUNNER_ENV_FILE", "~/.config/hive/runner.env")
    ).expanduser()
    values = load_stored_config(path)
    url = values.get("HIVE_URL", "").strip()
    if not url:
        return None
    return Target(url, _basic_auth(values.get("HIVE_BASIC_AUTH", "")), values.get("HIVE_TOKEN", ""))


def resolve_targets(env: dict[str, str], stored: dict[str, str]) -> list[Target]:
    """Chief candidates the CLI will try in order.

    An explicit URL names the one chief the operator means — no guessing
    beyond it. Precedence there is env var > stored config, so a one-off
    ``HIVE_URL=… hive …`` overrides the saved default the way a ``--context``
    flag would (the inverse of `prepare_run_env`'s server precedence on
    purpose: there stored config gives the *server* its own keys; here env
    gives the *operator* ad-hoc targeting).

    With no URL configured the CLI discovers: a chief on localhost first (the
    `hive run` dev loop), then the chief this machine's runner is installed
    against — so any machine in the fleet finds its chief out of the box."""

    def pick(key: str) -> str:
        return env.get(key) or stored.get(key, "")

    auth = _basic_auth(pick("HIVE_BASIC_AUTH"))
    token = pick("HIVE_TOKEN")
    if url := pick("HIVE_URL"):
        return [Target(url, auth, token)]
    targets = [Target(DEFAULT_HIVE_URL, auth, token)]
    if fallback := runner_env_target():
        targets.append(fallback)
    return targets


def _is_secret(key: str) -> bool:
    return (
        key.endswith("_TOKEN")
        or key.endswith("_API_KEY")
        or key.endswith("_SECRET")
        or key == "HIVE_BASIC_AUTH"
    )


def _mask(key: str, value: str) -> str:
    if not _is_secret(key) or not value:
        return value
    return f"…{value[-4:]}" if len(value) > 4 else "****"


def _gh_token(preferred_user: str = "") -> str:
    from hive._integrations.github_repos import gh_token_for

    return gh_token_for(preferred_user)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hive", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"hive {get_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="launch the local chief (auto-detects tokens)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    p.add_argument(
        "--no-web-build",
        action="store_true",
        help="serve the existing web bundle instead of rebuilding web/dist first",
    )

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

    p = sub.add_parser("doctor", help="run local preflight checks")
    dsub = p.add_subparsers(dest="doctor_command", required=True)
    dsub.add_parser("storage", help="check Firestore/GCS managed state access")

    p = sub.add_parser("migrate-local-state", help="copy a legacy local store to Firestore/GCS")
    p.add_argument("--data-dir", default=os.environ.get("HIVE_DATA_DIR", "/tmp/hive-data"))
    p.add_argument("--gcp-project", required=True)
    p.add_argument("--gcs-bucket", required=True)
    p.add_argument("--workspace-id", default="")
    p.add_argument("--no-verify", action="store_true", help="skip readback verification")

    sub.add_parser(
        "whoami",
        help="show the resolved chief target and current auth identity",
    )
    sub.add_parser("version", help="show the local CLI version and the target chief version")

    sub.add_parser("pause", help="pause all of hive: nothing new starts, running tasks finish")
    sub.add_parser("resume", help="undo `hive pause`; queued work dispatches again")
    p = sub.add_parser("projects", help="list projects")

    p = sub.add_parser(
        "new",
        help="one-step project start: create, wire or create the repo, hand over "
        "the spec, and launch the intake scout",
    )
    p.add_argument("name")
    p.add_argument("--spec", help="path to your spec document ('-' reads stdin)")
    p.add_argument("--repo", default="", help="existing repo git URL (default: create a private repo)")
    p.add_argument("--budget", type=float, help="daily spend cap in USD (default 10)")
    p.add_argument("--public", action="store_true", help="make the created repo public")

    p = sub.add_parser("repo-create", help="create a private greenfield repo for a project")
    p.add_argument("project_id")
    p.add_argument("--name", default="")
    p.add_argument("--public", action="store_true")

    p = sub.add_parser(
        "intake",
        help="the intake conversation, keyed by project: show it, answer it, or approve it",
        description="With no flags: show the scout's latest brief and what to do next "
        "(starts intake if none exists). -m sends an answer; --proceed accepts the "
        "assumptions; --approve finalizes (the scout pushes any missing spec files and "
        "planning wakes).",
    )
    p.add_argument("project_id")
    p.add_argument("-m", "--message", default="", help="answer or correct the scout")
    p.add_argument("--proceed", action="store_true", help="proceed with the scout's assumptions")
    p.add_argument("--approve", action="store_true", help="approve the brief; finalize and go")
    p.add_argument("--backend", default="", help="pin the scout backend when (re)starting intake")

    p = sub.add_parser("project", help="project detail: workstreams, tasks, questions")
    p.add_argument("project_id")

    p = sub.add_parser(
        "show",
        help="inspect hive's subsystems: machines, launchable agents, autonomy jobs",
    )
    p.add_argument(
        "part",
        nargs="?",
        choices=["machines", "agents", "subscriptions", "limits", "autonomy"],
        help="one subsystem (default: all)",
    )
    p.add_argument("--json", action="store_true", help="raw payload instead of the readable summary")

    p = sub.add_parser("set", help="patch project settings")
    p.add_argument("project_id")
    p.add_argument("--autonomy")
    p.add_argument("--ci-autofix", choices=["true", "false"], help="poll repo CI and auto-fix red builds")
    p.add_argument(
        "--testing-auto",
        choices=["true", "false"],
        help="autonomously draft/repair stories and sweep unproven ones (needs a daily budget)",
    )
    p.add_argument("--paused", choices=["true", "false"])
    p.add_argument("--daily-budget", type=float, help="daily cap on all paid work in USD (0 pauses it; new projects default to 10)")
    p.add_argument(
        "--grants",
        help="agent allowance as JSON, e.g. "
        '\'[{"sessions_per_day": 5}, {"backends": ["codex"], "models": ["gpt-5.4-mini"]}]\' '
        "(omit sessions_per_day for unlimited; '[]' clears back to no limits)",
    )
    p.add_argument("--member-repos", help="comma-separated git URLs (replaces the list)")
    p.add_argument("--spec-repo", help="spec home git URL")

    p = sub.add_parser(
        "ask",
        help="give Hive a task: files your ask as a GitHub issue and the issue "
        "pipeline works it to done",
    )
    p.add_argument("project_id")
    p.add_argument("text", help="what you want done ('-' reads stdin)")

    p = sub.add_parser("scan", help="scan the project's open GitHub issues and queue fixes")
    p.add_argument("project_id")

    p = sub.add_parser(
        "issue-run",
        help="run a workstream's GitHub issues: all open now, --issue picks some, --scan-only just mirrors",
    )
    p.add_argument("project_id")
    p.add_argument("workstream_id", help="the github_issues workstream for the repo (see `hive project`)")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument(
        "--issue", action="append", type=int, default=[],
        help="issue number to run (repeatable; implies selected scope)",
    )
    scope.add_argument(
        "--scan-only", action="store_true",
        help="record the run and mirror open issues without queueing fixes",
    )

    p = sub.add_parser("issue-sync", help="refresh a workstream's mirrored GitHub issues (no fixes queued)")
    p.add_argument("project_id")
    p.add_argument("workstream_id")

    p = sub.add_parser("preflight", help="check issue-solving preconditions (token, perms, runner push/gh auth)")
    p.add_argument("project_id")

    p = sub.add_parser("check-ci", help="check a repo's CI; file+fix an issue if it's red")
    p.add_argument("project_id")
    p.add_argument("workstream_id", help="the github_issues workstream for the repo (see `hive project`)")

    p = sub.add_parser("test-refresh", help="draft/align acceptance stories from the spec (no sweep)")
    p.add_argument("project_id")
    p.add_argument("workstream_id")
    p.add_argument("--backend", default="", help="agent backend (default: server config)")
    p.add_argument("--model", default="", help="model (default: backend default)")

    p = sub.add_parser("test-run", help="run a testing episode: refresh -> sweep as a user -> confirm -> file bugs")
    p.add_argument("project_id")
    p.add_argument("workstream_id")
    p.add_argument("--scope", choices=["priority", "full", "selected"], default="priority")
    p.add_argument("--story", action="append", default=[], help="story key to sweep (repeatable; implies --scope selected)")
    p.add_argument("--max", type=int, default=0, dest="max_stories", help="cap on stories swept (priority scope)")

    p = sub.add_parser("stories", help="testing coverage: stories x status, plus Hive's standing offer")
    p.add_argument("project_id")

    p = sub.add_parser("testability", help="testability contract: state, decisions needing you, Hive's offer")
    p.add_argument("project_id")

    p = sub.add_parser("testability-draft", help="have Hive explore the repo and draft/repair testability.md")
    p.add_argument("project_id")
    p.add_argument("workstream_id")
    p.add_argument("--backend", default="", help="agent backend (default: server config)")
    p.add_argument("--model", default="", help="model (default: backend default)")

    p = sub.add_parser("testability-probe", help="prove the contract: stand the app up per testability.md")
    p.add_argument("project_id")
    p.add_argument("workstream_id")
    p.add_argument("--backend", default="", help="agent backend (default: server config)")
    p.add_argument("--model", default="", help="model (default: backend default)")

    p = sub.add_parser("test-cancel", help="cancel a testing episode (dequeues pending, stops running tasks)")
    p.add_argument("episode_id")

    p = sub.add_parser("issue-cancel", help="cancel a GitHub issue run (dequeues pending, stops running tasks)")
    p.add_argument("run_id")

    p = sub.add_parser("iterate", help="start the next iteration with a note")
    p.add_argument("project_id")
    p.add_argument("note")

    p = sub.add_parser("plan", help="show the project's iteration plan (items + statuses)")
    p.add_argument("project_id")

    p = sub.add_parser("plan-propose", help="ask the planner to draft an iteration plan")
    p.add_argument("project_id")

    p = sub.add_parser("plan-new", help="create a hand-written draft plan")
    p.add_argument("project_id")
    p.add_argument("goal")
    p.add_argument("items", help="JSON list of {title,story,constraints,notes,repo}; '-' = stdin")

    p = sub.add_parser("plan-approve", help="approve all remaining items and start the plan")
    p.add_argument("project_id")

    p = sub.add_parser("plan-abandon", help="abandon the active plan (cancels its queued items and tasks)")
    p.add_argument("project_id")

    p = sub.add_parser("plan-item-add", help="add an item to the current plan (draft, or amendment on a live one)")
    p.add_argument("project_id")
    p.add_argument("title")
    p.add_argument("--story", default="")
    p.add_argument("--constraints", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--repo", default="")

    p = sub.add_parser("plan-item-edit", help="rewrite parts of a plan item; --order reorders")
    p.add_argument("item_id")
    p.add_argument("--title")
    p.add_argument("--story")
    p.add_argument("--constraints")
    p.add_argument("--notes")
    p.add_argument("--repo")
    p.add_argument("--order", type=int)

    p = sub.add_parser("plan-item-approve", help="approve one proposed plan item")
    p.add_argument("item_id")

    p = sub.add_parser("plan-item-unapprove", help="flip an approved (not yet queued) item back to unreviewed")
    p.add_argument("item_id")

    p = sub.add_parser("plan-retry", help="retry a blocked/rejected plan item")
    p.add_argument("item_id")

    p = sub.add_parser("plan-item-cancel", help="cancel a plan item (unblocks the queue behind it)")
    p.add_argument("item_id")
    p.add_argument("--reason", default="")

    p = sub.add_parser("answer", help="answer an open question")
    p.add_argument("question_id")
    p.add_argument("answer")

    p = sub.add_parser("dismiss", help="dismiss an open question without answering")
    p.add_argument("question_id")

    p = sub.add_parser(
        "decision-reopen",
        help="re-open a Hive-assumed ledger decision as a question to you",
    )
    p.add_argument("project_id")
    p.add_argument("decision_id", help="ledger entry id, e.g. D-002 (see `hive project` decision_ledger)")
    p.add_argument(
        "--workstream", default="",
        help="park only this workstream's work (default: every active manual workstream)",
    )

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

    p = sub.add_parser(
        "login",
        help="fix an agent login on a remote runner machine: SSH channel from here, OAuth in your local browser",
    )
    p.add_argument("backend", help="claude | codex | cursor (machine-bound logins)")
    p.add_argument("--machine", default=os.environ.get("HIVE_VM", "hive-vm"),
                   help="runner machine name as shown by `hive show machines` (GCE VM)")

    sub.add_parser("agents", help="list locally detected supported agent backends")
    sub.add_parser("resources", help="runners and backend resources")
    p = sub.add_parser("probe", help="probe one registered backend resource")
    p.add_argument("resource_id")
    p = sub.add_parser("resource-disable", help="park a backend resource: stays visible, out of dispatch")
    p.add_argument("resource_id")
    p.add_argument("--reason", default="", help="why it is parked (shown in `hive show agents`)")
    p = sub.add_parser("resource-enable", help="bring a parked backend resource back into dispatch")
    p.add_argument("resource_id")

    sub.add_parser("subs", help="list subscriptions")
    p = sub.add_parser("sub-add", help="add a subscription")
    p.add_argument("provider")
    p.add_argument("--plan", default="")
    p.add_argument(
        "--licensing",
        default="unknown",
        choices=("portable", "machine_bound", "unknown"),
        help="portable (API key, any machine) or machine_bound (login tied to one machine)",
    )
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

    sub.add_parser("users", help="workspace members: role, machines, licenses, open todos")
    p = sub.add_parser("user-role", help="set a member's role")
    p.add_argument("user_id", help="user id (see `hive users`)")
    p.add_argument("role", choices=("admin", "resource_provider"))
    p = sub.add_parser("machine-owner", help="claim/assign a machine to a user ('' releases)")
    p.add_argument("machine_id")
    p.add_argument("user_id", nargs="?", default="", help="empty = release")
    p = sub.add_parser(
        "machine-forget",
        help="forget a machine that is gone for good (drops its runners, resources, checkouts)",
    )
    p.add_argument("machine_id")

    sub.add_parser(
        "enroll-token",
        help="mint a one-hour enrollment token (+ the `hive enroll` command) for onboarding a new machine",
    )
    p = sub.add_parser(
        "enroll",
        help="onboard THIS machine as a runner owned by you (token from `hive enroll-token`)",
    )
    p.add_argument("--url", required=True, help="chief URL, e.g. https://hive.example.com")
    p.add_argument("--token", required=True, help="enrollment token (`hive enroll-token` or the web machines page)")
    p.add_argument("--name", default="", help="runner name (default: short hostname)")

    return parser


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


# How to run each machine-bound login on a remote runner. The runner services
# run as root (deploy/vm_startup.sh: HOME=/root) and the SSH lands as root, so
# credentials go straight to root's home. The OAuth interaction itself stays on
# the operator's machine: URLs print into the SSH'd terminal and are opened in
# the LOCAL browser; codex additionally needs its localhost callback port
# forwarded through the tunnel.
LOGIN_RECIPES: dict[str, dict] = {
    "claude": {
        "remote": "claude auth login",
        "forwards": (),
        "coach": (
            "claude prints a login URL. Open it in your LOCAL browser, pick the "
            "subscription account, authorize, then paste the code back into the "
            "terminal."
        ),
    },
    "codex": {
        "remote": "codex login",
        "forwards": ("-L", "1455:localhost:1455"),
        "coach": (
            "codex prints a http://localhost:1455/... URL. Open it in your "
            "LOCAL browser — the SSH session forwards that port to the VM."
        ),
    },
    "cursor": {
        "remote": "cursor-agent login",
        "forwards": (),
        "coach": (
            "cursor-agent prints a login URL. Open it in your LOCAL browser "
            "and finish the flow; the CLI picks the session up."
        ),
    },
}


def login_ssh_argv(backend: str, machine: str, env: dict[str, str]) -> list[str]:
    """The SSH invocation for one login recipe. The hive VM is reached as root
    at its stable DNS name (override with HIVE_VM_HOST; any other machine name
    is used as the host directly); `-t` allocates the TTY the interactive
    login needs."""
    recipe = LOGIN_RECIPES[backend]
    default_host = "hive.tachyon-ai.eu" if machine == "hive-vm" else machine
    host = env.get("HIVE_VM_HOST", default_host)
    return ["ssh", "-t", *recipe["forwards"], f"root@{host}", recipe["remote"]]


def _human_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


def _fmt_machines(rows: list[dict]) -> list[str]:
    out = ["MACHINES"]
    for m in rows:
        if m["online"]:
            state = "online"
        elif m["retired"]:
            state = "retired"
        elif m["dark"]:
            state = f"DARK {_human_duration(time.time() - m['last_seen'])}"
        else:
            state = f"offline {_human_duration(time.time() - m['last_seen'])}"
        chief = " [chief]" if m["hosts_chief"] else ""
        backends = ", ".join(sorted({b for r in m["runners"] for b in r["backends"]})) or "-"
        out.append(f"  {m['name']:<12} {m['device_kind']}/{m['os']:<8} {state:<12}{chief} {backends}")
    return out if len(out) > 1 else out + ["  (none)"]


def _fmt_agents(data: dict) -> list[str]:
    out = [f"AGENTS — {data['launchable_now']} of {len(data['agents'])} launchable now"]
    for a in data["agents"]:
        note = f" — {a['note']}" if a["note"] else ""
        out.append(f"  {a['status']:<9} {a['backend']:<11} @ {a['machine']}{note}")
    return out


def _fmt_subscriptions(data: dict) -> list[str]:
    out = ["SUBSCRIPTIONS — what you own vs where it actually works"]
    for s in data["subscriptions"]:
        plan = f" {s['plan']}" if s["plan"] else ""
        serving = ", ".join(s["serving"]) or "NOWHERE"
        out.append(f"  {s['provider']:<11} ({s['licensing_mode']}){plan} — serving: {serving}")
        for gap in s["login_needed"]:
            fix = (
                f"hive login {s['provider']} --machine {gap['machine']}"
                if s["provider"] in LOGIN_RECIPES and s["licensing_mode"] == "machine_bound"
                else f"provide the key/login on {gap['machine']} (portable)"
            )
            out.append(f"      missing on {gap['machine']}: {gap['note']}  ->  {fix}")
    if not data["subscriptions"]:
        out.append("  (none recorded)")
    if data["unregistered"]:
        out.append("unregistered — worked in a probe but not recorded (`hive sub-add <provider>`)")
        for c in data["unregistered"]:
            out.append(f"  {c['provider']:<11} {c['evidence']}")
    if data["unowned"]:
        out.append(f"unowned — no subscription, usable nowhere: {', '.join(data['unowned'])}")
    return out


def _local_stamp(epoch: float) -> str:
    return time.strftime("%a %H:%M", time.localtime(epoch))


def _reset_stamp(epoch: float) -> str:
    """A future reset moment, unambiguous across weeks: weekly windows reset
    up to 7 days out, where a bare weekday name reads as today."""
    delta = epoch - time.time()
    form = "%d %b %H:%M" if delta > 6 * 86400 else "%a %H:%M"
    stamp = time.strftime(form, time.localtime(epoch))
    return f"{stamp} (in {_human_duration(delta)})" if delta > 0 else stamp


def _fmt_limits(rows: list[dict]) -> list[str]:
    out = ["LIMITS — what each license knows about its own usage windows"]
    for r in rows:
        if not r["windows"] and not r["exhaustions_seen"]:
            out.append(
                f"  {r['backend']:<11} @ {r['machine']:<12} no usage gauge — empirical only, no limit hit yet"
            )
            continue
        plan = f" [{r['plan']}]" if r["plan"] else ""
        age = f", snapshot {_human_duration(r['snapshot_age_s'])} old" if r["captured_at"] else ""
        out.append(f"  {r['backend']:<11} @ {r['machine']}{plan} (via {r['source']}{age})")
        for w in r["windows"]:
            resets = _reset_stamp(w["resets_at"]) if w["resets_at"] else "?"
            extra = ""
            if w.get("hive_tokens_in_window"):
                extra += f" — hive spent ~{w['hive_tokens_in_window']:,} tok"
            if w.get("estimated_tokens_left"):
                extra += f", est ~{w['estimated_tokens_left']:,} tok left"
            out.append(
                f"      {w['kind']:<14} {w['used_percent']:>3.0f}% used, resets {resets}{extra}"
            )
        if r["cooldown_until"]:
            out.append(f"      cooling down until {_reset_stamp(r['cooldown_until'])}")
        if r["last_exhaustion"]:
            e = r["last_exhaustion"]
            hint = f" (message said resets {_local_stamp(e['reset_at_hint'])})" if e["reset_at_hint"] else ""
            out.append(
                f"      hit limit {r['exhaustions_seen']}x, last {_local_stamp(e['at'])}: {e['text']}{hint}"
            )
    if not rows:
        out.append("  (no agents discovered)")
    return out


def _fmt_autonomy(rows: list[dict]) -> list[str]:
    out = ["AUTONOMY"]
    for j in rows:
        where = f" [{j['project_name']}]" if j["project_id"] else ""
        head = f"  {j['job']}{where} every {_human_duration(j['interval_s'])}"
        if j["action_now"]:
            via = ""
            if j["backends"]:
                machines = ", ".join(j["machines"]) or "NO MACHINE AVAILABLE"
                via = f" via {'/'.join(j['backends'])} on {machines}"
            out.append(f"{head}{via}: {j['action_now']}")
        else:
            out.append(f"{head}: idle — {j['reason']}")
    return out


def format_show(payload, part: str | None) -> str:
    """Readable rendering of the /api/show payload (or one selected part)."""
    if part == "machines":
        return "\n".join(_fmt_machines(payload))
    if part == "agents":
        return "\n".join(_fmt_agents(payload))
    if part == "subscriptions":
        return "\n".join(_fmt_subscriptions(payload))
    if part == "limits":
        return "\n".join(_fmt_limits(payload))
    if part == "autonomy":
        return "\n".join(_fmt_autonomy(payload))
    return "\n".join(
        _fmt_machines(payload["machines"])
        + [""]
        + _fmt_agents(payload["agents"])
        + [""]
        + _fmt_subscriptions(payload["subscriptions"])
        + [""]
        + _fmt_limits(payload["limits"])
        + [""]
        + _fmt_autonomy(payload["autonomy"])
    )


def run_login(args: argparse.Namespace, client) -> dict:
    """Channel a machine-bound agent login through the operator's machine:
    open the SSH session, let the human do the OAuth locally, then probe the
    resource so success is proven (and its 'Fix login' todo auto-closes)."""
    backend = args.backend
    if backend not in LOGIN_RECIPES:
        raise SystemExit(
            f"`hive login` handles interactive machine-bound logins ({', '.join(sorted(LOGIN_RECIPES))}). "
            f"`{backend}` uses a portable API key — set it in the runner's environment instead."
        )
    data = client.get("/api/resources").raise_for_status().json()
    machine = next((m for m in data["machines"] if m["name"] == args.machine), None)
    if machine is None:
        known = ", ".join(m["name"] for m in data["machines"]) or "(none)"
        raise SystemExit(f"unknown machine {args.machine!r}; known machines: {known}")
    resource = next(
        (r for r in data["resources"]
         if r["machine_id"] == machine["id"] and r["backend"] == backend),
        None,
    )

    recipe = LOGIN_RECIPES[backend]
    argv = login_ssh_argv(backend, args.machine, dict(os.environ))
    print(f"Logging in `{backend}` on {args.machine} — the browser part happens HERE, locally.", file=sys.stderr)
    print(f"  {recipe['coach']}", file=sys.stderr)
    print(f"  $ {' '.join(argv)}\n", file=sys.stderr)
    ssh_exit = subprocess.call(argv)

    result: dict = {"backend": backend, "machine": args.machine, "ssh_exit_code": ssh_exit}
    if resource is None:
        result["probe"] = "skipped: that machine has no discovered resource for this backend"
        return result
    print("Verifying with a probe…", file=sys.stderr)
    probe = client.post(f"/api/resources/{resource['id']}/probe")
    if probe.status_code == 409:
        result["probe"] = f"not started: {probe.json().get('detail', 'runner offline')}"
        return result
    task = probe.raise_for_status().json()["task"]
    for _ in range(90):
        polled = client.get(f"/api/tasks/{task['id']}").raise_for_status().json()
        if polled["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(2)
    fresh = next(
        r for r in client.get("/api/resources").raise_for_status().json()["resources"]
        if r["id"] == resource["id"]
    )
    result["probe"] = fresh["usability_status"]
    if fresh["usability_status"] == "usable":
        result["message"] = "Login proven usable; the matching 'Fix login' todo closes automatically."
    elif fresh["last_probe_text"]:
        result["note"] = fresh["last_probe_text"].splitlines()[0]
    return result


def prepare_run_env(env: dict[str, str], stored: dict[str, str]) -> list[str]:
    """Resolve the tokens/settings the chief will run with, mutating
    `env`, and return human-readable lines (with provenance) describing them.

    Precedence, highest first: hive's own `stored` config, then ambient env,
    then autodetection (`gh auth token`). Stored config intentionally *overrides*
    ambient env so a user can give hive separate keys — e.g. to bill/track its
    cost on a different account — while autodetected tokens are just the starting
    point you seed that store from (`hive config import`).

    Client-target keys (`CLIENT_KEYS`) are skipped: they describe where the CLI
    *sends* commands, not how this server *runs*, so they never leak into the
    chief process."""
    stored = {k: v for k, v in stored.items() if v and k not in CLIENT_KEYS}
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
        notes.append("store: MISSING HIVE_GCP_PROJECT (Firestore is required)")

    if env.get("HIVE_GCS_BUCKET"):
        notes.append(f"blobs: GCS ({env['HIVE_GCS_BUCKET']}, from {src('HIVE_GCS_BUCKET')})")
    else:
        notes.append("blobs: MISSING HIVE_GCS_BUCKET (GCS is required)")

    notes.append(
        f"workspace: {env.get('HIVE_WORKSPACE_ID', 'default')} "
        f"({env.get('HIVE_WORKSPACE_NAME', 'ikamen')})"
    )
    if env.get("HIVE_PUBLIC_URL"):
        notes.append(f"public url: {env['HIVE_PUBLIC_URL']}")

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


def _managed_state_missing(env: dict[str, str]) -> list[str]:
    return [key for key in ("HIVE_GCP_PROJECT", "HIVE_GCS_BUCKET") if not env.get(key, "").strip()]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_web_dist() -> Path:
    return _repo_root() / "web" / "dist"


def _web_deps_stale(web_dir: Path) -> bool:
    """Best-effort dependency freshness check before building the SPA."""
    node_modules = web_dir / "node_modules"
    installed_lock = node_modules / ".package-lock.json"
    package_lock = web_dir / "package-lock.json"
    if not node_modules.is_dir() or not installed_lock.is_file():
        return True
    return package_lock.is_file() and package_lock.stat().st_mtime > installed_lock.stat().st_mtime


def _run_checked(cmd: list[str], cwd: Path, description: str) -> None:
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n{description} failed with exit code {exc.returncode}.", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc


def _prepare_web_bundle(skip_build: bool) -> None:
    web_dir = _repo_root() / "web"
    dist_dir = _default_web_dist()
    if configured := os.environ.get("HIVE_WEB_DIST", "").strip():
        configured_dist = Path(configured).expanduser()
        if configured_dist.resolve() != dist_dir.resolve():
            print(f"web: using HIVE_WEB_DIST={configured}")
            if not skip_build:
                print("web: build skipped because HIVE_WEB_DIST is custom")
            return

    os.environ["HIVE_WEB_DIST"] = str(dist_dir)
    if skip_build:
        print(f"web: serving existing bundle from {dist_dir}")
        return

    if not shutil.which("npm"):
        print(
            "\nCannot build the web UI because `npm` is not on PATH.\n"
            "Install Node/npm, or rerun with `uv run hive run --no-web-build` "
            "to serve the existing web/dist bundle.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if _web_deps_stale(web_dir):
        print("web: installing npm dependencies")
        _run_checked(["npm", "ci"], web_dir, "npm ci")
    print("web: building latest web bundle")
    _run_checked(["npm", "run", "build"], web_dir, "npm run build")


def _run_chief(args: argparse.Namespace) -> None:
    import uvicorn

    from hive.runner._local import local_chief_url

    os.environ.setdefault("HIVE_PUBLIC_URL", local_chief_url(args.host, args.port))
    for line in prepare_run_env(os.environ, load_stored_config()):
        print(f"  {line}")
    if missing := _managed_state_missing(os.environ):
        print(
            "\nHive requires managed state.\n"
            f"Missing: {', '.join(missing)}\n\n"
            "Set:\n"
            "  HIVE_GCP_PROJECT=<gcp-project>\n"
            "  HIVE_GCS_BUCKET=<gcs-bucket>\n\n"
            "Then run:\n"
            "  gcloud auth application-default login\n"
            "  uv run hive run",
            file=sys.stderr,
        )
        raise SystemExit(2)
    _prepare_web_bundle(skip_build=args.no_web_build)
    print(f"starting hive chief {get_version()} on {args.host}:{args.port}\n")
    try:
        uvicorn.run(
            "hive.api:production_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
            timeout_graceful_shutdown=UVICORN_GRACEFUL_SHUTDOWN_S,
        )
    except RuntimeError as exc:
        if "leader lease" in str(exc):
            print(f"Hive chief did not start: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        raise


def _run_doctor(args: argparse.Namespace) -> None:
    from hive.config.settings import Config
    from hive.config.storage import managed_state_doctor

    prepare_run_env(os.environ, load_stored_config())
    if args.doctor_command == "storage":
        result = managed_state_doctor(Config.from_env())
    else:
        raise AssertionError(f"unhandled doctor command {args.doctor_command}")
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


def _run_migrate_local_state(args: argparse.Namespace) -> None:
    from hive.persistence.blobstore import LocalBlobStore
    from hive.config.storage import migrate_local_state
    from hive.persistence.store import FileStore

    data_dir = Path(args.data_dir).expanduser()
    stored = load_stored_config()
    workspace_id = (
        args.workspace_id
        or stored.get("HIVE_WORKSPACE_ID")
        or os.environ.get("HIVE_WORKSPACE_ID", "default")
    )
    result = migrate_local_state(
        FileStore(data_dir / "store"),
        LocalBlobStore(data_dir / "blobs"),
        gcp_project=args.gcp_project,
        gcs_bucket=args.gcs_bucket,
        workspace_id=workspace_id,
        verify=not args.no_verify,
    )
    print(json.dumps(result, indent=2))


def stories_report(detail: dict) -> dict:
    """Condense a project payload into the testing coverage view: per testing
    workstream, the backlog health (with Hive's standing offer) and one row per
    active story."""
    health = detail.get("testing_health", {})
    stories = detail.get("stories", [])
    episodes = sorted(detail.get("test_episodes", []), key=lambda e: e["created_at"], reverse=True)
    report = []
    for stream in detail.get("workstreams", []):
        if stream["kind"] != "testing":
            continue
        report.append(
            {
                "workstream_id": stream["id"],
                "repo": stream["repo"],
                "health": health.get(stream["id"], {}),
                "latest_episode": next(
                    (
                        {k: e[k] for k in ("id", "status", "scope", "story_keys", "created_at")}
                        for e in episodes
                        if e["workstream_id"] == stream["id"]
                    ),
                    None,
                ),
                "stories": [
                    {
                        "key": s["key"],
                        "status": s["status"],
                        "oracle": s["oracle_status"],
                        "fidelity": s["last_fidelity"],
                        "last_tested_at": s["last_tested_at"],
                        "issue": s["open_issue_url"] or None,
                    }
                    for s in stories
                    if s["workstream_id"] == stream["id"] and s["status"] != "archived"
                ],
            }
        )
    return {"testing": report}


def testability_report(detail: dict) -> dict:
    """Condense a project payload into the testability view: per testing
    workstream, the contract state with Hive's standing offer, plus every open
    decision question waiting on the human."""
    views = detail.get("testability", {})
    questions = detail.get("questions", [])
    report = []
    for stream in detail.get("workstreams", []):
        if stream["kind"] != "testing":
            continue
        view = views.get(stream["id"], {})
        contract = view.get("contract") or {}
        report.append(
            {
                "workstream_id": stream["id"],
                "repo": stream["repo"],
                "health": view.get("health", {}),
                "status": contract.get("status", "missing"),
                "fidelities": contract.get("fidelities", []),
                "probed_fidelity": contract.get("probed_fidelity", ""),
                "probe_problems": contract.get("probe_problems", []),
                "decisions": [
                    {"question_id": q["id"], "text": q["text"]}
                    for q in questions
                    if q["workstream_id"] == stream["id"]
                    and q.get("dedup_key", "").startswith("testability:")
                    and q["status"] == "open"
                ],
            }
        )
    return {"testability": report}


def _plan_id(client, project_id: str) -> str:
    """Resolve the project's live plan id (plan routes key on it)."""
    detail = client.get(f"/api/projects/{project_id}").raise_for_status().json()
    payload = detail.get("plan")
    if not payload:
        raise SystemExit("no plan for this project — `hive plan-propose` or `hive plan-new` first")
    return payload["plan"]["id"]


def run(args: argparse.Namespace, client) -> dict | list:
    """Execute one command against an httpx-compatible client and return the
    response payload. Non-2xx responses raise (clear failure over silence)."""
    c = args.command
    if c == "whoami":
        me = client.get("/api/auth/me").raise_for_status().json()
        return {"target": str(client.base_url), "cli_version": version_payload(), **me}
    elif c == "version":
        payload = {"target": str(client.base_url), "cli": version_payload()}
        try:
            payload["chief"] = client.get("/api/version").raise_for_status().json()
        except Exception as exc:
            payload["chief"] = None
            payload["chief_error"] = str(exc)
        return payload
    elif c in ("pause", "resume"):
        r = client.patch("/api/workspace", json={"paused": c == "pause"})
    elif c == "projects":
        r = client.get("/api/projects")
    elif c == "new":
        spec_text = ""
        if args.spec:
            spec_text = sys.stdin.read() if args.spec == "-" else Path(args.spec).read_text()
        project = client.post(
            "/api/projects", json={"name": args.name, "spec_text": spec_text}
        ).raise_for_status().json()
        pid = project["id"]
        if args.repo:
            repo = args.repo
            client.patch(
                f"/api/projects/{pid}",
                json={"spec_repo": repo, "member_repos": [repo]},
            ).raise_for_status()
        else:
            created = client.post(
                f"/api/projects/{pid}/repo", json={"name": "", "private": not args.public}
            ).raise_for_status().json()
            repo = created["repo"]["full_name"]
        if args.budget is not None:
            client.patch(
                f"/api/projects/{pid}", json={"daily_budget_usd": args.budget}
            ).raise_for_status()
        conversation = client.post(
            f"/api/projects/{pid}/intake/start"
        ).raise_for_status().json()
        return {
            "project_id": pid,
            "repo": repo,
            "conversation_id": conversation["id"],
            "scout": f"{conversation['backend']} {conversation.get('model', '')}".strip(),
            "next": (
                f"The scout is reading your spec. `hive intake {pid}` shows its brief; "
                f"answer with `hive intake {pid} -m '<text>'`; approve with "
                f"`hive intake {pid} --approve`."
            ),
        }
    elif c == "repo-create":
        r = client.post(f"/api/projects/{args.project_id}/repo", json={
            "name": args.name,
            "private": not args.public,
        })
    elif c == "intake":
        detail = client.get(f"/api/projects/{args.project_id}").raise_for_status().json()
        conversation_id = detail["project"].get("intake_conversation_id", "")
        conversation = next(
            (c_ for c_ in detail.get("conversations", []) if c_["id"] == conversation_id),
            None,
        )
        wants_action = bool(args.message or args.proceed or args.approve)
        # No conversation, or a failed one being retried, (re)starts the scout.
        if conversation is None or (
            conversation["status"] == "failed" and (wants_action or args.backend)
        ):
            conversation = client.post(
                f"/api/projects/{args.project_id}/intake/start",
                json={"backend": args.backend},
            ).raise_for_status().json()
            if not wants_action:
                return {
                    "conversation": conversation,
                    "next": "the scout is reading the spec — rerun `hive intake` for its brief",
                }
        if args.approve:
            r = client.post(f"/api/conversations/{conversation['id']}/message", json={"action": "approve"})
        elif args.proceed:
            r = client.post(f"/api/conversations/{conversation['id']}/message", json={"action": "proceed"})
        elif args.message:
            r = client.post(
                f"/api/conversations/{conversation['id']}/message",
                json={"action": "message", "message": args.message},
            )
        else:
            status = conversation.get("status", "")
            hints = {
                "open": "answer with `-m '<text>'`, accept assumptions with --proceed, or --approve",
                "running": "the scout is working — rerun `hive intake` shortly",
                "finalizing": "approved — the scout is pushing the spec files",
                "done": "intake is complete; planning owns the project now",
                "failed": "intake failed — retry with `hive intake <project> --backend <scout>`",
            }
            return {
                "status": status,
                "scout": f"{conversation.get('backend', '')} {conversation.get('model', '')}".strip(),
                "brief": conversation.get("latest_brief", ""),
                "next": hints.get(status, ""),
            }
    elif c == "project":
        r = client.get(f"/api/projects/{args.project_id}")
    elif c == "show":
        data = client.get("/api/show").raise_for_status().json()
        return data[args.part] if args.part else data
    elif c == "set":
        body = {}
        if args.autonomy is not None:
            body["autonomy"] = args.autonomy
        for flag in ("ci_autofix", "testing_auto", "paused"):
            if (v := getattr(args, flag)) is not None:
                body[flag] = v == "true"
        if args.daily_budget is not None:
            body["daily_budget_usd"] = args.daily_budget
        if args.grants is not None:
            body["agent_grants"] = json.loads(args.grants)
        if args.member_repos is not None:
            body["member_repos"] = _csv(args.member_repos)
        if args.spec_repo is not None:
            body["spec_repo"] = args.spec_repo
        r = client.patch(f"/api/projects/{args.project_id}", json=body)
    elif c == "ask":
        text = sys.stdin.read() if args.text == "-" else args.text
        r = client.post(f"/api/projects/{args.project_id}/directives", json={"text": text})
    elif c == "plan":
        detail = client.get(f"/api/projects/{args.project_id}").raise_for_status().json()
        return detail.get("plan") or {
            "note": "no plan yet — `hive plan-propose` asks the AI, `hive plan-new` writes one by hand"
        }
    elif c == "plan-propose":
        r = client.post(f"/api/projects/{args.project_id}/plan/propose")
    elif c == "plan-new":
        items = json.loads(sys.stdin.read() if args.items == "-" else args.items)
        r = client.post(
            f"/api/projects/{args.project_id}/plan", json={"goal": args.goal, "items": items}
        )
    elif c == "plan-approve":
        r = client.post(f"/api/plans/{_plan_id(client, args.project_id)}/approve")
    elif c == "plan-abandon":
        r = client.post(f"/api/plans/{_plan_id(client, args.project_id)}/abandon")
    elif c == "plan-item-add":
        r = client.post(
            f"/api/plans/{_plan_id(client, args.project_id)}/items",
            json={"title": args.title, "story": args.story, "constraints": args.constraints,
                  "notes": args.notes, "repo": args.repo},
        )
    elif c == "plan-item-edit":
        body = {
            k: getattr(args, k)
            for k in ("title", "story", "constraints", "notes", "repo", "order")
            if getattr(args, k) is not None
        }
        r = client.patch(f"/api/plan-items/{args.item_id}", json=body)
    elif c == "plan-item-approve":
        r = client.post(f"/api/plan-items/{args.item_id}/approve")
    elif c == "plan-item-unapprove":
        r = client.post(f"/api/plan-items/{args.item_id}/unapprove")
    elif c == "plan-retry":
        r = client.post(f"/api/plan-items/{args.item_id}/retry")
    elif c == "plan-item-cancel":
        r = client.post(f"/api/plan-items/{args.item_id}/cancel", json={"reason": args.reason})
    elif c == "scan":
        r = client.post(f"/api/projects/{args.project_id}/scan-issues")
    elif c == "issue-run":
        r = client.post(
            f"/api/projects/{args.project_id}/workstreams/{args.workstream_id}/issue-runs",
            json={
                "scope": "selected" if args.issue
                else ("scan_only" if args.scan_only else "all_open_now"),
                "issue_numbers": args.issue,
            },
        )
    elif c == "issue-sync":
        r = client.post(f"/api/projects/{args.project_id}/workstreams/{args.workstream_id}/sync")
    elif c == "check-ci":
        r = client.post(
            f"/api/projects/{args.project_id}/workstreams/{args.workstream_id}/check-ci"
        )
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
    elif c == "test-refresh":
        r = client.post(
            f"/api/projects/{args.project_id}/workstreams/{args.workstream_id}/test-refresh",
            json={"backend": args.backend, "model": args.model},
        )
    elif c == "test-run":
        r = client.post(
            f"/api/projects/{args.project_id}/workstreams/{args.workstream_id}/test-episodes",
            json={
                "scope": "selected" if args.story else args.scope,
                "story_keys": args.story,
                "max_stories": args.max_stories,
            },
        )
    elif c == "stories":
        detail = client.get(f"/api/projects/{args.project_id}").raise_for_status().json()
        return stories_report(detail)
    elif c == "testability":
        detail = client.get(f"/api/projects/{args.project_id}").raise_for_status().json()
        return testability_report(detail)
    elif c == "testability-draft":
        r = client.post(
            f"/api/projects/{args.project_id}/workstreams/{args.workstream_id}/testability-draft",
            json={"backend": args.backend, "model": args.model},
        )
    elif c == "testability-probe":
        r = client.post(
            f"/api/projects/{args.project_id}/workstreams/{args.workstream_id}/testability-probe",
            json={"backend": args.backend, "model": args.model},
        )
    elif c == "test-cancel":
        r = client.post(f"/api/test-episodes/{args.episode_id}/cancel")
    elif c == "issue-cancel":
        r = client.post(f"/api/issue-runs/{args.run_id}/cancel")
    elif c == "iterate":
        r = client.patch(f"/api/projects/{args.project_id}",
                         json={"new_iteration_note": args.note})
    elif c == "answer":
        r = client.post(f"/api/questions/{args.question_id}/answer",
                        json={"answer": args.answer})
    elif c == "dismiss":
        r = client.post(f"/api/questions/{args.question_id}/dismiss")
    elif c == "decision-reopen":
        r = client.post(
            f"/api/projects/{args.project_id}/decisions/{quote(args.decision_id, safe='')}/reopen",
            json={"workstream_id": args.workstream},
        )
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
        from hive.agents import BACKEND_NAMES
        from hive.runner._daemon import discovery_payload

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
    elif c == "login":
        return run_login(args, client)
    elif c == "resource-disable":
        r = client.patch(f"/api/resources/{args.resource_id}",
                         json={"enabled": False, "disabled_reason": args.reason})
    elif c == "resource-enable":
        r = client.patch(f"/api/resources/{args.resource_id}", json={"enabled": True})
    elif c == "subs":
        r = client.get("/api/subscriptions")
    elif c == "sub-add":
        r = client.post("/api/subscriptions", json={
            "provider": args.provider, "plan": args.plan,
            "licensing_mode": args.licensing, "notes": args.notes,
        })
    elif c == "sub-rm":
        r = client.delete(f"/api/subscriptions/{args.sub_id}")
    elif c == "todos":
        r = client.get("/api/human-todos")
    elif c == "todo-add":
        r = client.post("/api/human-todos", json={
            "title": args.title, "instructions": args.instructions,
            "project_id": args.project_id,
        })
    elif c == "todo-done":
        r = client.post(f"/api/human-todos/{args.task_id}/done")
    elif c == "org-context":
        r = client.get("/api/org-context")
    elif c == "org-context-set":
        text = args.text if args.text is not None else sys.stdin.read()
        r = client.put("/api/org-context", json={"text": text})
    elif c == "users":
        r = client.get("/api/users")
    elif c == "user-role":
        r = client.patch(f"/api/users/{args.user_id}", json={"role": args.role})
    elif c == "machine-owner":
        r = client.patch(f"/api/machines/{args.machine_id}", json={"owner_user_id": args.user_id})
    elif c == "machine-forget":
        r = client.delete(f"/api/machines/{args.machine_id}")
    elif c == "enroll-token":
        r = client.post("/api/enroll-tokens")
    else:
        raise AssertionError(f"unhandled command {c}")
    r.raise_for_status()
    return r.json()


def _run_enroll(args: argparse.Namespace) -> None:
    """Onboard this machine as a runner: exchange the enrollment token for
    runner credentials, then install the service (macOS launchd via
    `deploy/install_mac_runner.sh`; elsewhere just materialize runner.env).
    The chief claims the machine for the token's minting user on first
    register, so their login todos route correctly from day one."""
    import socket

    import httpx

    url = args.url.rstrip("/")
    basic = os.environ.get("HIVE_BASIC_AUTH", "") or load_stored_config().get("HIVE_BASIC_AUTH", "")
    auth = tuple(basic.split(":", 1)) if basic else None
    name = args.name.strip() or socket.gethostname().split(".")[0]

    response = httpx.post(f"{url}/api/enroll", json={"token": args.token}, auth=auth, timeout=30.0)
    if response.status_code in (401, 403):
        # The chief answers JSON; a bare body means the Caddy perimeter, i.e.
        # the request never reached hive.
        if "application/json" not in response.headers.get("content-type", ""):
            raise SystemExit(
                "the chief's perimeter refused the request (basic auth). Set "
                "HIVE_BASIC_AUTH=user:pass (the site password you use in the browser) and retry."
            )
        detail = response.json().get("detail", response.text[:300])
        raise SystemExit(
            f"enrollment refused: {detail}\n"
            "Tokens expire after an hour — mint a fresh one with `hive enroll-token` "
            "(or from the web machines page)."
        )
    response.raise_for_status()
    creds = response.json()
    if not creds["gh_token"]:
        # Without it the mac installer would fall back to its Secret Manager
        # path, which a non-admin laptop has no access to.
        raise SystemExit(
            "the chief has no GitHub token configured (HIVE_GH_TOKEN); "
            "ask the admin to set it, then enroll again."
        )

    env = {
        **os.environ,
        "HIVE_URL": url,
        "HIVE_RUNNER_TOKEN": creds["runner_token"],
        "HIVE_GH_TOKEN": creds["gh_token"],
        "HIVE_BASIC_AUTH": basic,
        "HIVE_RUNNER_NAME": name,
        "HIVE_RUNNER_OWNER": creds["owner_user_id"],
    }
    if sys.platform == "darwin":
        script = _repo_root() / "deploy" / "install_mac_runner.sh"
        if not script.is_file():
            raise SystemExit(f"installer not found at {script} — run from a hive checkout")
        subprocess.run(["bash", str(script)], env=env, check=True)
        print(f"\nenrolled: this laptop serves Hive as runner '{name}', claimed for you.")
        print("It appears on the machines page within a minute; agent logins are probed automatically.")
        return

    state_dir = Path(os.environ.get("HIVE_RUNNER_STATE_DIR", "~/.config/hive")).expanduser()
    env_file = state_dir / "runner.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"HIVE_URL={url}",
        f"HIVE_RUNNER_TOKEN={creds['runner_token']}",
        f"HIVE_RUNNER_NAME={name}",
        f"HIVE_GH_TOKEN={creds['gh_token']}",
        f"HIVE_RUNNER_OWNER={creds['owner_user_id']}",
    ]
    if basic:
        lines.append(f"HIVE_BASIC_AUTH={basic}")
    env_file.write_text("\n".join(lines) + "\n")
    env_file.chmod(0o600)
    print(f"wrote {env_file} (chmod 600)")
    print("start the runner with:")
    print(f"  set -a; source {env_file}; set +a; python -m hive.runner")


def main(argv: list[str] | None = None) -> None:
    import httpx

    args = build_parser().parse_args(argv)
    if args.command == "run":
        _run_chief(args)
        return
    if args.command == "enroll":
        _run_enroll(args)
        return
    if args.command == "config":
        _run_config(args)
        return
    if args.command == "doctor":
        _run_doctor(args)
        return
    if args.command == "migrate-local-state":
        _run_migrate_local_state(args)
        return
    targets = resolve_targets(os.environ, load_stored_config())
    last_error: httpx.RequestError | None = None
    for i, target in enumerate(targets):
        client = httpx.Client(
            base_url=target.base_url,
            auth=target.auth,
            headers={"Authorization": f"Bearer {target.token}"} if target.token else {},
            timeout=30.0,
        )
        try:
            if args.command == "trace":
                # Raw JSONL, not JSON-wrapped, so it pipes into kodo's viewer / jq.
                response = client.get(f"/api/tasks/{args.task_id}/trace")
                if response.status_code == 404:
                    print(
                        "no trace recorded for this task (it may have failed "
                        "before an agent ran, e.g. at checkout)",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
                print(response.raise_for_status().text)
                return
            payload = run(args, client)
            if args.command == "show" and not args.json:
                print(format_show(payload, args.part))
            else:
                print(json.dumps(payload, indent=2))
            return
        except httpx.HTTPStatusError as exc:
            # A status code means we found a chief here; other candidates are moot.
            code = exc.response.status_code
            if code in (401, 403):
                print(
                    f"Not authorized at {target.base_url} (HTTP {code}). Set credentials with "
                    "`hive config set HIVE_BASIC_AUTH user:pass` (chief behind basic auth) "
                    "or `hive config set HIVE_TOKEN …` (app-level auth).",
                    file=sys.stderr,
                )
            else:
                print(f"Hive API error {code} at {target.base_url}: {exc.response.text}", file=sys.stderr)
            raise SystemExit(1) from exc
        except httpx.RequestError as exc:
            # Quiet fall-through: discovery walking past a dead candidate is
            # normal (no localhost chief on a fleet machine), not news worth a
            # line on every command. Total failure below names what was tried.
            last_error = exc
    print(
        f"Hive API unreachable (tried {', '.join(t.base_url for t in targets)}): {last_error}",
        file=sys.stderr,
    )
    raise SystemExit(1) from last_error


if __name__ == "__main__":
    main()
