"""The desktop installer wires the managed CLI into a user's shell."""

import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "deploy/install_mac_desktop.py"


def test_installed_cli_uses_service_environment_and_preserves_arguments(tmp_path):
    """Installing twice leaves one callable CLI, even with spaces in paths."""
    home = tmp_path / "user home"
    service = tmp_path / "service clone"
    cli = service / ".venv/bin/hive"
    cli.parent.mkdir(parents=True)
    cli.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    cli.chmod(0o755)
    for _ in range(2):
        subprocess.run(
            [sys.executable, str(INSTALLER), "--home", str(home), "--service-repo", str(service)],
            check=True,
        )
    result = subprocess.run(
        [str(home / ".local/bin/hive"), "a b", "$(false)", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == ["a b", "$(false)", "--help"]
    bundle = home / "Applications/Hive.app/Contents"
    info = plistlib.loads((bundle / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Hive"
    assert os.access(bundle / "MacOS" / info["CFBundleExecutable"], os.X_OK)


@pytest.mark.parametrize("loaded", [False, True])
def test_opening_app_restores_service_without_restarting_it(tmp_path, loaded):
    """Exercise the installed app, with launchctl replaced at the OS boundary.

    Hidden services are loaded; a visible icon keeps its existing process.
    Neither path touches the runner or any pause flags.
    """
    home = tmp_path / "user home"
    service = tmp_path / "service"
    cli = service / ".venv/bin/hive"
    cli.parent.mkdir(parents=True)
    cli.touch()
    subprocess.run(
        [sys.executable, str(INSTALLER), "--home", str(home), "--service-repo", str(service)],
        check=True,
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl = fake_bin / "launchctl"
    launchctl.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$1" >> "$CALLS"\n'
        'if [ "$1" = print ]; then exit "$PRINT_RESULT"; fi\n'
        'if [ "$1" = bootstrap ]; then test -f "$3"; fi\n'
    )
    launchctl.chmod(0o755)
    plist = home / "Library/LaunchAgents/com.hive.menubar.plist"
    plist.parent.mkdir(parents=True)
    plist.touch()
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "CALLS": str(calls),
        "PRINT_RESULT": "0" if loaded else "113",
    }
    app = home / "Applications/Hive.app/Contents/MacOS/Hive"
    subprocess.run([str(app)], env=env, check=True)
    assert calls.read_text().splitlines() == (
        ["print", "kickstart"] if loaded else ["print", "bootstrap", "kickstart"]
    )
    if not loaded:
        # Missing service installation must fail visibly, not claim success.
        plist.unlink()
        result = subprocess.run([str(app)], env=env, capture_output=True)
        assert result.returncode != 0
