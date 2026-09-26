"""`python -m hive.agents` — this machine's agent CLIs, without hive.

    python -m hive.agents                          survey installed CLIs + quota gauges
    python -m hive.agents run [--dir D] TASK|-     run one agent task in a directory

`run` defaults to OpenCode on its free model, confined to that one model, so
delegating a task costs nothing. The agent's final answer goes to stdout; one
status line (backend, session handle, tokens, time, failure class) to stderr.
Exit 1 when the run errored or ended without a final answer.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from hive.agents import classify_failure, discover_backends, run_agent
from hive.agents.backends import BACKEND_NAMES, opencode_model
from hive.agents.usage import collect_usage


def survey() -> None:
    for discovery in discover_backends():
        state = discovery.version if discovery.installed else "not installed"
        line = f"{discovery.name:<11} {discovery.status:<8} {state}"
        if discovery.message:
            line += f"  ({discovery.message})"
        print(line)
        if discovery.installed and (snapshot := collect_usage(discovery.name)):
            print(json.dumps(snapshot, indent=1))


def run(args: argparse.Namespace) -> int:
    task = sys.stdin.read() if args.task == "-" else args.task
    model = args.model or (opencode_model() if args.backend == "opencode" else "")
    started = time.monotonic()
    result = run_agent(
        args.backend, task, Path(args.dir).resolve(), model=model,
        resume_session=args.resume, timeout_s=args.timeout,
    )
    print(result.text)
    status = [
        f"{args.backend} {model or 'default'}",
        f"session {result.session_handle or 'n/a'}",
        f"tokens {result.input_tokens} in / {result.output_tokens} out",
        f"{time.monotonic() - started:.0f}s",
    ]
    if result.is_error:
        status.append(f"ERROR {classify_failure(result.text, is_error=True) or 'unclassified'}")
    elif result.incomplete_reason:
        status.append(f"INCOMPLETE {result.incomplete_reason}")
    print(" · ".join(status), file=sys.stderr)
    return 1 if result.is_error or result.incomplete_reason else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m hive.agents", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command")
    p = commands.add_parser("run", help="run one agent task in a directory")
    p.add_argument("task", help="the instructions, or - to read them from stdin")
    p.add_argument("--dir", default=".", help="working directory the agent edits (default: cwd)")
    p.add_argument("--backend", default="opencode", choices=BACKEND_NAMES)
    p.add_argument("--model", default="", help=f"default for opencode: {opencode_model()}")
    p.add_argument("--resume", default="", help="session handle from a previous run's status line")
    p.add_argument("--timeout", type=float, default=900.0,
                   help="seconds of agent silence before giving up (default: 900)")
    args = parser.parse_args()
    if args.command == "run":
        return run(args)
    survey()
    return 0


if __name__ == "__main__":
    sys.exit(main())
