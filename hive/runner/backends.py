"""Agent backend registry: the single source of truth for the kodo coding
agents Hive can run.

Adding a backend means adding one `Backend` entry here; everything else (the
orchestrator's allowed-backend list, the runner's session factory, probe
support, capability detection) derives from `REGISTRY`. Session factories import
`kodo` lazily so the control plane can import this module without kodo present —
only the runner machine needs the agent CLIs installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

PROBE_MARKER = "HIVE_AGENT_PROBE_OK"
PREFLIGHT_TIMEOUT_S = 15.0

AUTH_WARNING_PATTERNS = re.compile(
    r"auth|login|credential|api.?key|not authenticated|forbidden|permission|unauthori[sz]ed",
    re.IGNORECASE,
)
SUBSCRIPTION_WARNING_PATTERNS = re.compile(
    r"subscription|billing|quota|usage.?limit|plan.?limit|rate.?limit|too many requests|429\b",
    re.IGNORECASE,
)

# A spent rate-limit/quota window: the credential works, the backend is just
# throttled for a while. Hive cools the resource down and retries later.
EXHAUSTION_PATTERNS = re.compile(
    r"rate.?limit|quota|usage.?limit|plan.?limit|too many requests|429\b|credits?",
    re.IGNORECASE,
)
# A login/policy block: the credential is rejected or the account is not allowed
# to run this agent (e.g. "organization has disabled Claude subscription access",
# "use an Anthropic API key", expired login, forbidden). Unlike exhaustion this
# does not heal on its own — a human must fix the login/policy — so Hive marks the
# resource failed and files an operator todo instead of a silent cooldown.
AUTH_BLOCK_PATTERNS = re.compile(
    r"subscription access|use an? .*api key|ask your admin|not authenticated|"
    r"unauthori[sz]ed|forbidden|invalid api key|expired|log ?in|credential",
    re.IGNORECASE,
)


def classify_failure(text: str, *, is_error: bool) -> str:
    """Classify a finished agent result for capacity accounting.

    Returns ``"auth"`` for a login/policy block (needs a human), ``"exhausted"``
    for a transient rate-limit/quota window, or ``""`` otherwise. Auth wins over
    exhaustion: a message that trips both (e.g. "rate limited; please re-login")
    is the safer-to-escalate case, so we treat it as an auth block.
    """
    if not is_error:
        return ""
    if AUTH_BLOCK_PATTERNS.search(text):
        return "auth"
    if EXHAUSTION_PATTERNS.search(text):
        return "exhausted"
    return ""


def _claude(model: str, resume_session: str = ""):
    from kodo.sessions.claude import ClaudeSession

    kwargs = {"resume_session_id": resume_session} if resume_session else {}
    return ClaudeSession(model=model, **kwargs) if model else ClaudeSession(**kwargs)


def _cursor(model: str, resume_session: str = ""):
    from kodo.sessions.cursor import CursorSession

    kwargs = {"resume_chat_id": resume_session} if resume_session else {}
    return CursorSession(model=model, **kwargs) if model else CursorSession(**kwargs)


def _codex(model: str, resume_session: str = ""):
    from kodo.sessions.codex import CodexSession

    sandbox = "danger-full-access"
    kwargs = {"resume_session_id": resume_session} if resume_session else {}
    return (
        CodexSession(model=model, sandbox=sandbox, **kwargs)
        if model
        else CodexSession(sandbox=sandbox, **kwargs)
    )


def _gemini_cli(model: str, resume_session: str = ""):
    from kodo.sessions.gemini_cli import GeminiCliSession

    kwargs = {"resume_session": True} if resume_session else {}
    return GeminiCliSession(model=model, **kwargs) if model else GeminiCliSession(**kwargs)


@dataclass(frozen=True)
class Backend:
    """One coding-agent backend. `make_session(model)` builds a kodo session
    (empty model = the backend's own default). `binary`/`preflight` describe
    the runner-side discovery strategy; the actual usability proof is still a
    Hive probe task that launches the agent against a throwaway repository."""

    name: str
    make_session: Callable[[str, str], object]
    binary: str
    preflight: tuple[str, ...]
    login_hint: str
    # Provider-rulebook default for how this backend's credential is licensed:
    # "portable" (an API key Hive can copy anywhere) or "machine_bound" (a login
    # tied to the machine that authed). Best-effort and evolving; mirrors
    # models.LicensingMode. See wiki/architecture.md (provider rulebook).
    licensing: str = "unknown"


@dataclass(frozen=True)
class BackendDiscovery:
    """Best-effort runner-local discovery for one backend.

    `installed` means Hive found the CLI on PATH. `status`/`message` are
    diagnostics only; a successful probe is what makes a resource dispatchable.
    """

    name: str
    installed: bool
    status: str
    path: str = ""
    version: str = ""
    message: str = ""


REGISTRY: dict[str, Backend] = {
    b.name: b
    for b in (
        Backend(
            "claude",
            _claude,
            binary="claude",
            preflight=("claude", "--version"),
            login_hint="Run `claude login` on the runner, then let Hive probe it again.",
            licensing="machine_bound",  # Claude Max login is bound to the machine that authed
        ),
        Backend(
            "cursor",
            _cursor,
            binary="cursor-agent",
            preflight=("cursor-agent", "--version"),
            login_hint=(
                "Refresh the Cursor Agent login on the runner "
                "(`cursor-agent login` if available), then let Hive probe it again."
            ),
            licensing="portable",  # Cursor API key spends subscription quota on any machine
        ),
        Backend(
            "codex",
            _codex,
            binary="codex",
            preflight=("codex", "--version"),
            login_hint="Run `codex login` on the runner, then let Hive probe it again.",
            licensing="machine_bound",  # ChatGPT/codex login is a per-machine OAuth device flow
        ),
        Backend(
            "gemini-cli",
            _gemini_cli,
            binary="gemini",
            preflight=("gemini", "--version"),
            login_hint=(
                "Run `gemini auth login` or set `GEMINI_API_KEY` for the runner, "
                "then let Hive probe it again."
            ),
            licensing="portable",  # a GEMINI_API_KEY works on any machine
        ),
    )
}

BACKEND_NAMES: tuple[str, ...] = tuple(REGISTRY)


def make_session(backend: str, model: str = "", resume_session: str = ""):
    """Build a kodo session for `backend`. Raises on an unknown backend rather
    than guessing — the registry is the contract."""
    if backend not in REGISTRY:
        raise ValueError(f"unknown backend {backend!r}; known: {BACKEND_NAMES}")
    return REGISTRY[backend].make_session(model, resume_session)


def backend_licensing(backend: str) -> str:
    """The provider-rulebook licensing default for `backend`.

    Unknown backends are `"unknown"` rather than an error: licensing is advisory
    capacity metadata, not the dispatch contract.
    """
    entry = REGISTRY.get(backend)
    return entry.licensing if entry else "unknown"


def _snippet(text: str, limit: int = 500) -> str:
    return " ".join(text.split())[:limit]


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _warning_from_output(text: str) -> str:
    if AUTH_WARNING_PATTERNS.search(text):
        return "authentication issue detected by preflight"
    if SUBSCRIPTION_WARNING_PATTERNS.search(text):
        return "subscription, billing, or quota issue detected by preflight"
    return ""


def discover_backend(backend: Backend) -> BackendDiscovery:
    """Detect one backend without spending model quota.

    This intentionally stops at "CLI appears runnable enough to try". Some
    CLIs reveal auth/subscription problems in preflight output, but many do
    not, so `probe_instructions()` remains the authoritative availability
    check.
    """
    path = shutil.which(backend.binary)
    if not path:
        return BackendDiscovery(
            name=backend.name,
            installed=False,
            status="missing",
            message=f"`{backend.binary}` was not found on PATH",
        )

    try:
        proc = subprocess.run(
            list(backend.preflight),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PREFLIGHT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return BackendDiscovery(
            name=backend.name,
            installed=True,
            status="warning",
            path=path,
            version="timeout",
            message=f"preflight timed out after {PREFLIGHT_TIMEOUT_S:.0f}s",
        )
    except OSError as exc:
        return BackendDiscovery(
            name=backend.name,
            installed=True,
            status="error",
            path=path,
            message=str(exc),
        )

    combined = f"{proc.stderr}\n{proc.stdout}"
    warning = _warning_from_output(combined)
    version = _first_line(proc.stdout) or _first_line(proc.stderr)
    if proc.returncode == 0:
        return BackendDiscovery(
            name=backend.name,
            installed=True,
            status="warning" if warning else "ok",
            path=path,
            version=version or "ok",
            message=warning,
        )

    return BackendDiscovery(
        name=backend.name,
        installed=True,
        status="warning" if warning else "error",
        path=path,
        version=version or f"exit {proc.returncode}",
        message=warning or _snippet(combined) or f"preflight exited {proc.returncode}",
    )


def discover_backends() -> list[BackendDiscovery]:
    """Discover every backend Hive knows about, in registry order."""
    return [discover_backend(backend) for backend in REGISTRY.values()]


def detected_backend_names(discoveries: list[BackendDiscovery] | None = None) -> list[str]:
    """Names that should be advertised by a runner.

    A backend is advertised when its CLI is installed. Dispatch still requires
    a successful Hive probe, so an installed-but-unauthenticated CLI is visible
    to the operator without being used for project work.
    """
    discoveries = discoveries if discoveries is not None else discover_backends()
    return [d.name for d in discoveries if d.installed and d.name in REGISTRY]


def probe_instructions(backend: str) -> str:
    """The usability-probe prompt: prove the backend can read the repo and reply
    without mutating it, ending with PROBE_MARKER on its own line."""
    return (
        f"Hive agent usability probe for backend `{backend}`.\n"
        "Do not modify files, create commits, push branches, or change repository state.\n"
        "Inspect the repository only if needed, then reply with this exact marker on its own line:\n"
        f"{PROBE_MARKER}\n"
        "No markdown, no extra commentary."
    )
