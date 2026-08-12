#!/usr/bin/env python3
"""Scaleway spend killswitch: trip before the monthly credits run out.

Reads month-to-date consumption from the billing API and, once it crosses
``--kill-at``, powers off the hive project's running instances and deletes the
hive API key. Stdlib only, so it runs from a bare cron on the laptop or a
systemd timer on the VM without an install step.

Two things are worth knowing before relying on this:

- Billing consumption lags (the API reports its own ``updated_at``; it has been
  seen minutes to hours behind). The gap between ``--kill-at`` and the actual
  credit ceiling is the safety margin for that lag, so keep it wide.
- Deleting an API key stops *new* provisioning; it does not stop the meter on
  what is already running. Powering off the instances is what stops the spend,
  which is why that runs first and the key deletion runs last.

Deleting an API key is irreversible: the secret cannot be read back, only
replaced by minting a new one and redeploying it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = "https://api.scaleway.com"
ORG = "17efcf03-4911-41f9-a059-4bd6fe2f3ffe"  # Covenance.ai Srl
HIVE_PROJECT = "d56850e6-57a4-4e0f-b503-9b6e785a05d2"
HIVE_KEY = "SCWFMPV6YAADMPTYA0HX"
ZONES = ("fr-par-1", "fr-par-2", "fr-par-3", "nl-ams-1", "nl-ams-2", "nl-ams-3", "pl-waw-1", "pl-waw-2", "pl-waw-3")


class Scaleway:
    """Thin Scaleway API client. Errors surface as urllib.error.HTTPError."""

    def __init__(self, token: str) -> None:
        self.token = token

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{API}/{path}",
            data=data,
            method=method,
            headers={"X-Auth-Token": self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else {}

    def consumptions(self, organization_id: str) -> dict:
        return self._call("GET", f"billing/v2beta1/consumptions?organization_id={organization_id}")

    def servers(self, zone: str, project: str) -> list[dict]:
        return self._call("GET", f"instance/v1/zones/{zone}/servers?project={project}").get("servers", [])

    def poweroff(self, zone: str, server_id: str) -> None:
        self._call("POST", f"instance/v1/zones/{zone}/servers/{server_id}/action", {"action": "poweroff"})

    def delete_api_key(self, access_key: str) -> None:
        self._call("DELETE", f"iam/v1alpha1/api-keys/{access_key}")


def euros(value: dict) -> float:
    """Scaleway money (units + nanos) as a float. Discount lines are negative.

    >>> euros({"units": 11, "nanos": 760000000})
    11.76
    >>> euros({"units": 0, "nanos": -800000000})
    -0.8
    """
    return round(value["units"] + value["nanos"] / 1e9, 9)


@dataclass
class Spend:
    """Month-to-date consumption, org-wide and split per project."""

    total: float
    by_project: dict[str, float] = field(default_factory=dict)
    updated_at: str = ""

    def scoped(self, project_name: str | None) -> float:
        """Spend counting toward the threshold: org-wide, or one project's share."""
        return self.total if project_name is None else self.by_project.get(project_name, 0.0)


def read_spend(payload: dict) -> Spend:
    """Fold a /consumptions response into totals.

    >>> read_spend({"consumptions": [
    ...     {"value": {"units": 10, "nanos": 0}, "project_name": "hive"},
    ...     {"value": {"units": 0, "nanos": -500000000}, "project_name": "hive"},
    ... ], "updated_at": "2026-08-12T08:18:21Z"}).total
    9.5
    """
    spend = Spend(total=0.0, updated_at=payload.get("updated_at", ""))
    for line in payload.get("consumptions", []):
        amount = euros(line["value"])
        spend.total = round(spend.total + amount, 9)
        name = line.get("project_name", "?")
        spend.by_project[name] = round(spend.by_project.get(name, 0.0) + amount, 9)
    return spend


def verdict(scoped: float, warn_at: float, kill_at: float) -> str:
    """Which band the spend falls in: ok < warn <= warn < kill <= kill.

    >>> [verdict(s, 800, 1000) for s in (0, 799.99, 800, 999.99, 1000, 1200)]
    ['ok', 'ok', 'warn', 'warn', 'kill', 'kill']
    """
    if scoped >= kill_at:
        return "kill"
    if scoped >= warn_at:
        return "warn"
    return "ok"


def find_token() -> str:
    """Secret key from the env, the scw CLI config, or the VM's hive env file."""
    if token := os.environ.get("SCW_SECRET_KEY"):
        return token
    for path, key in ((Path.home() / ".config/scw/config.yaml", "secret_key:"), (Path("/etc/hive/scw.env"), "SCW_SECRET_KEY=")):
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip().startswith(key):
                    return line.split(key, 1)[1].strip().strip("\"'")
    sys.exit("no Scaleway secret key: set SCW_SECRET_KEY, or configure the scw CLI")


def trip(scw: Scaleway, project: str, access_key: str, dry_run: bool, delete_key: bool = True) -> list[str]:
    """Stop the meter, then optionally remove the key. Order matters: deleting the
    key we authenticate with revokes our own ability to power anything off.

    Key deletion is off when running on the agent VM. Deleting a key needs org-wide
    IAM rights, and a box where agents run as root is the last place to put those —
    the poweroffs are the part that actually stops the spend.
    """
    actions = []
    for zone in ZONES:
        for server in scw.servers(zone, project):
            if server["state"] != "running":
                continue
            actions.append(f"poweroff {server['name']} ({server['commercial_type']}, {zone})")
            if not dry_run:
                scw.poweroff(zone, server["id"])
    if delete_key:
        actions.append(f"delete api key {access_key}")
        if not dry_run:
            scw.delete_api_key(access_key)
    else:
        actions.append(f"keep api key {access_key} (--no-delete-key)")
    return actions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kill-at", type=float, default=1000.0, help="EUR month-to-date that trips the killswitch (default: 1000)")
    p.add_argument("--warn-at", type=float, default=None, help="EUR that logs a warning (default: 80%% of --kill-at)")
    p.add_argument("--scope", default="org", help="'org' for the whole organization's credit burn, or a project name (e.g. 'hive')")
    p.add_argument("--project", default=HIVE_PROJECT, help="project whose instances get powered off when tripped")
    p.add_argument("--kill-key", default=HIVE_KEY, help="access key to delete when tripped")
    p.add_argument("--no-delete-key", action="store_true", help="power off only; skip key deletion (needs org IAM rights the agent VM must not have)")
    p.add_argument("--dry-run", action="store_true", help="report what would happen, touch nothing")
    args = p.parse_args(argv)

    warn_at = args.warn_at if args.warn_at is not None else args.kill_at * 0.8
    scope = None if args.scope == "org" else args.scope

    scw = Scaleway(find_token())
    spend = read_spend(scw.consumptions(ORG))
    scoped = spend.scoped(scope)
    state = verdict(scoped, warn_at, args.kill_at)

    breakdown = ", ".join(f"{name} {amount:.2f}" for name, amount in sorted(spend.by_project.items()))
    print(f"[{state}] {args.scope} spend EUR {scoped:.2f} of {args.kill_at:.0f} (warn {warn_at:.0f}) — {breakdown}")
    print(f"billing data as of {spend.updated_at}")

    if state != "kill":
        return {"ok": 0, "warn": 1}[state]

    for action in trip(scw, args.project, args.kill_key, args.dry_run, delete_key=not args.no_delete_key):
        print(f"{'would ' if args.dry_run else ''}{action}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
