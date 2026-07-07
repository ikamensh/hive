"""Who is this machine: OS/arch/type detection and a stable identity.

`machine_metadata()` answers "what kind of host am I on" (overridable via
`HIVE_MACHINE_*` env vars); `stable_machine_id()` turns a human-chosen machine
name into a deterministic id, so re-registrations reuse the same identity.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from collections.abc import Mapping


def stable_machine_id(name: str, scope: str = "default") -> str:
    """Deterministic machine id for a named machine within a scope (e.g. a
    workspace). The same (scope, name) always maps to the same id, so restarts
    and re-enrollments converge on one machine record."""
    digest = hashlib.sha256(f"{scope}:{name}".encode()).hexdigest()[:16]
    return f"machine-{digest}"


def detect_machine_os() -> str:
    system = platform.system().strip().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def detect_machine_arch() -> str:
    return platform.machine().strip() or "unknown"


def _mac_model() -> str:
    try:
        return subprocess.run(
            ["sysctl", "-n", "hw.model"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def infer_machine_type(machine_os: str) -> str:
    os_name = machine_os.lower()
    if os_name == "macos":
        model = _mac_model().lower()
        if "macbook" in model:
            return "macbook"
        if "macmini" in model:
            return "mac-mini"
        if "macstudio" in model:
            return "mac-studio"
        if "imac" in model:
            return "imac"
        if "mac" in model:
            return "mac"
        return "mac"
    if os_name == "windows":
        return "win"
    if os_name == "linux":
        return "linux"
    return os_name or "unknown"


def infer_device_kind(machine_type: str, machine_os: str) -> str:
    type_name = machine_type.lower()
    os_name = machine_os.lower()
    if type_name == "macbook":
        return "laptop"
    if type_name in {"mac-mini", "mac-studio"}:
        return "server"
    if os_name == "linux":
        return "server"
    if os_name in {"macos", "windows"}:
        return "laptop"
    return "unknown"


def machine_metadata(env: Mapping[str, str] | None = None) -> dict[str, str]:
    if env is None:
        env = os.environ
    machine_os = env.get("HIVE_MACHINE_OS", "").strip() or detect_machine_os()
    machine_arch = env.get("HIVE_MACHINE_ARCH", "").strip() or detect_machine_arch()
    machine_type = env.get("HIVE_MACHINE_TYPE", "").strip() or infer_machine_type(machine_os)
    machine_kind = env.get("HIVE_MACHINE_KIND", "").strip() or infer_device_kind(
        machine_type,
        machine_os,
    )
    return {
        "machine_os": machine_os,
        "machine_arch": machine_arch,
        "machine_type": machine_type,
        "machine_kind": machine_kind,
    }
