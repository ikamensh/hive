"""Usability probes: prove a backend can actually work on this machine.

Discovery (`discover_backends`) only says a CLI is installed; a probe launches
the real agent against a throwaway local git repository and demands the marker
reply plus an untouched tree. That catches the failures that matter in
practice — expired logins, spent quotas, broken wrappers — without risking any
real repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hive.agents.backends import PROBE_MARKER, probe_instructions
from hive.agents.results import AgentCallResult
from hive.agents.run import run_agent

PROBE_TIMEOUT_S = 600.0


def _git(args: list[str], cwd: Path, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def ensure_probe_repo(workdir: Path) -> Path:
    """A throwaway local git repo built under `workdir` for usability probes.

    Probes only need *some* clean git repo to prove a backend can run and leave
    the tree tidy. Building it locally — rather than cloning anything — means a
    probe needs no shared filesystem and no network, so it works on any machine."""
    path = workdir / "agent-probe-repo"
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        _git(["init", "-b", "main"], path, 60)
        _git(["config", "user.email", "hive-probe@example.invalid"], path)
        _git(["config", "user.name", "Hive Probe"], path)
        (path / "README.md").write_text(
            "# Hive agent probe\n\nThis repository is only for backend usability checks.\n"
        )
        _git(["add", "README.md"], path)
        _git(["commit", "-m", "Initial probe repo"], path)
    else:  # reuse across probes, but always start from a clean tree
        _git(["reset", "--hard"], path, 60)
        _git(["clean", "-fd"], path, 60)
    return path


def validate_probe_result(
    probe_repo: Path,
    text: str,
    is_error: bool,
    *,
    backend: str = "",
) -> tuple[str, bool]:
    """Turn an agent's probe reply into a verdict: marker present, repo clean."""
    diagnostics = []
    if PROBE_MARKER not in text:
        diagnostics.append(f"probe marker {PROBE_MARKER!r} was not found in the agent reply")
    if backend == "codex" and "--full-auto" in text and "deprecated" in text.lower():
        diagnostics.append(
            "codex-cli is installed, but the kodo Codex wrapper invoked the deprecated "
            "`--full-auto` mode and returned no assistant message; update the wrapper to "
            "use the current `codex exec --sandbox ...` interface"
        )
    dirty = _git(["status", "--porcelain"], probe_repo, 30).stdout.strip()
    if dirty:
        diagnostics.append(f"probe left repository changes:\n{dirty}")
    if diagnostics:
        return f"{text}\n\nHIVE PROBE FAILED:\n" + "\n".join(f"- {d}" for d in diagnostics), True
    if not is_error:
        text = f"{text}\n\nHIVE PROBE PASSED: backend replied with {PROBE_MARKER} and left the repo clean."
    return text, is_error


def probe_backend(
    backend: str,
    workdir: Path,
    *,
    model: str = "",
    timeout_s: float = PROBE_TIMEOUT_S,
) -> AgentCallResult:
    """Run the full usability probe for one backend: build the throwaway repo,
    launch the agent, validate marker + clean tree. Spends one tiny agent turn."""
    repo = ensure_probe_repo(workdir)
    result = run_agent(
        backend,
        probe_instructions(backend),
        repo,
        model=model,
        timeout_s=timeout_s,
        agent_name=f"{backend}-probe",
    )
    result.text, result.is_error = validate_probe_result(
        repo, result.text, result.is_error, backend=backend
    )
    return result
