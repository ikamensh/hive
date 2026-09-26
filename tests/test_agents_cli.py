"""`python -m hive.agents run` — delegate one task to an agent CLI from any shell.

This is the entrypoint the personal `opencode` skill drives, so it is pinned the
way a caller sees it: a subprocess against a fake `opencode` on PATH. The
default must be the free OpenCode model (delegating costs nothing), the answer
goes to stdout, and the session handle to stderr so a follow-up can resume.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAKE_OPENCODE = f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
if sys.argv[1:3] == ["debug", "config"]:
    print(os.environ["OPENCODE_CONFIG_CONTENT"])
    sys.exit(0)
args = sys.argv[1:]
if not sys.stdin.isatty():
    sys.stdin.read()  # like the real CLI: piped stdin is read to EOF
workdir = Path(args[args.index("--dir") + 1])
(workdir / "calls.jsonl").open("a").write(json.dumps(args) + "\\n")
if "fail" in args[-1]:
    print(json.dumps({"type": "error", "error": {"data": {"message": "Rate limit exceeded", "statusCode": 429}}}))
    sys.exit(1)
print(json.dumps({"type": "step_start", "sessionID": "ses_fake", "part": {}}))
print(json.dumps({"type": "text", "sessionID": "ses_fake", "part": {"text": "answer to: " + args[-1]}}))
print(json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 7, "output": 3}}}))
'''


def run_cli(tmp_path, *args, stdin=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    cli = bin_dir / "opencode"
    cli.write_text(FAKE_OPENCODE)
    cli.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env.pop("HIVE_OPENCODE_MODEL", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "hive.agents", "run", *args],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=ROOT,
    )
    if stdin is not None:
        proc.stdin.write(stdin)
        proc.stdin.close()
    # Otherwise stdin stays an open pipe, as from a shell tool that never closes it.
    try:
        proc.wait(timeout=60)
    finally:
        proc.kill()
    result = subprocess.CompletedProcess(proc.args, proc.returncode, proc.stdout.read(), proc.stderr.read())
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        stream.close()
    return result


def calls(workdir: Path) -> list[list[str]]:
    return [json.loads(line) for line in (workdir / "calls.jsonl").read_text().splitlines()]


def test_run_delegates_to_free_opencode_and_reports_session(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    proc = run_cli(tmp_path, "--dir", str(work), "fix the bug")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "answer to: fix the bug"
    assert "ses_fake" in proc.stderr
    [args] = calls(work)
    assert args[args.index("--model") + 1].endswith("-free")
    assert "--pure" in args  # free model is isolated: no fallback to paid models


def test_run_reads_task_from_stdin_and_resumes_a_session(tmp_path):
    proc = run_cli(tmp_path, "--dir", str(tmp_path), "--resume", "ses_fake", "-",
                   stdin="multi\nline task\n")

    assert proc.returncode == 0, proc.stderr
    [args] = calls(tmp_path)
    assert args[args.index("--session") + 1] == "ses_fake"
    assert args[-1].strip() == "multi\nline task"


def test_failed_run_exits_nonzero_with_its_failure_class(tmp_path):
    proc = run_cli(tmp_path, "--dir", str(tmp_path), "please fail")

    assert proc.returncode == 1
    assert "Rate limit exceeded" in proc.stdout
    assert "exhausted" in proc.stderr
