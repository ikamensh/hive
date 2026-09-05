"""Run the operator's checks and certify exactly the clean commit they tested."""

import os
from contextlib import suppress
from pathlib import Path
import signal
import subprocess
import tempfile

from hive.models import ValidationResult

OUTPUT_LIMIT = 8000


def validate_checkout(
    path: Path, command: str, branch: str, *, timeout_s: float = 600,
) -> ValidationResult:
    def git(*args):
        return subprocess.run(["git", *args], cwd=path, text=True, capture_output=True,
                              check=True, timeout=60).stdout.strip()

    def fail(message, exit_code=-1):
        return ValidationResult(command=command, exit_code=exit_code, output=message[-OUTPUT_LIMIT:])

    if git("branch", "--show-current") != branch:
        return fail(f"Validation requires branch {branch}")
    if git("status", "--porcelain"):
        return fail("Validation requires a clean checkout; commit uncommitted files first")
    sha = git("rev-parse", "HEAD")
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(command, shell=True, cwd=path, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out, exit_code = True, -1
        finally:
            # Also unwinds on Ctrl-C/SIGTERM and kills background children of
            # a shell that already exited. A finished group may already be gone.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - OUTPUT_LIMIT))
        text = output.read().decode(errors="replace")
    if timed_out:
        return fail(text + f"\nValidation timed out after {timeout_s:g}s: {command}")
    if exit_code:
        return fail(text, exit_code)
    if (git("status", "--porcelain") or git("rev-parse", "HEAD") != sha
            or git("branch", "--show-current") != branch):
        return fail(text + "\nValidation changed the checkout; commit fixes and rerun")
    remote = git("ls-remote", "origin", f"refs/heads/{branch}")
    if not remote or remote.split()[0] != sha:
        return fail(text + "\nValidated commit is not the pushed branch head; push it and rerun")
    return ValidationResult(command=command, exit_code=0, output=text, commit_sha=sha)
