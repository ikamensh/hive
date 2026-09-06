#!/usr/bin/env python3
"""Install the Spotlight app and PATH entry for an existing Mac runner.

Can be run separately without reloading services or fetching credentials:
    python3 deploy/install_mac_desktop.py
"""

import argparse
import os
from pathlib import Path
import plistlib
import shlex


def install(home: Path, service_repo: Path) -> Path:
    cli = service_repo / ".venv/bin/hive"
    if not cli.is_file():
        raise FileNotFoundError(f"Install the Mac runner first: {cli} is missing")

    command = home / ".local/bin/hive"
    command.parent.mkdir(parents=True, exist_ok=True)
    if command.is_symlink():
        command.unlink()
    command.symlink_to(cli)

    app = home / "Applications/Hive.app"
    contents = app / "Contents"
    executable = contents / "MacOS/Hive"
    executable.parent.mkdir(parents=True, exist_ok=True)
    plist = home / "Library/LaunchAgents/com.hive.menubar.plist"
    executable.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'domain="gui/$(id -u)"\n'
        'service="$domain/com.hive.menubar"\n'
        'if ! launchctl print "$service" >/dev/null 2>&1; then\n'
        f'  launchctl bootstrap "$domain" {shlex.quote(str(plist))}\n'
        "fi\n"
        'exec launchctl kickstart "$service"\n'
    )
    executable.chmod(0o755)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "eu.tachyon-ai.hive.launcher",
                "CFBundleName": "Hive",
                "CFBundleDisplayName": "Hive",
                "CFBundleExecutable": "Hive",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
                "LSUIElement": True,
            }
        )
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--service-repo", type=Path)
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    service_repo = (
        (
            args.service_repo
            or Path(os.environ.get("HIVE_SERVICE_REPO", str(home / ".local/share/hive-runner")))
        )
        .expanduser()
        .resolve()
    )
    app = install(home, service_repo)
    print(f"Installed {app} — open Hive from Spotlight to restore the menu bar icon.")
    print(f"Installed {home / '.local/bin/hive'} — add ~/.local/bin to PATH if needed.")


if __name__ == "__main__":
    main()
