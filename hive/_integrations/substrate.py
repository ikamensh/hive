"""Substrate power control: the chief switching capability machines on and off.

One driver per provider; Scaleway is the only one so far. The supervisor holds
a `Substrate | None` (None in tests and on chiefs without credentials) and
calls it from the wake / idle-stop paths. Deliberately tiny: power on, power
off, read state — machine *creation* stays an operator script
(`deploy/create_runner_vm.sh`), because it needs secrets and judgment; the
expensive part to automate is the 24/7 compute bill, and that is exactly
on/off.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("hive.substrate")

API = "https://api.scaleway.com/instance/v1/zones/{zone}/servers/{server_id}"


class ScalewaySubstrate:
    """Instance power actions with the IAM key the chief already holds."""

    def __init__(self, secret_key: str) -> None:
        self._headers = {"X-Auth-Token": secret_key}

    def power_on(self, zone: str, instance_id: str) -> None:
        self._action(zone, instance_id, "poweron")

    def power_off(self, zone: str, instance_id: str) -> None:
        self._action(zone, instance_id, "poweroff")

    def state(self, zone: str, instance_id: str) -> str:
        """Scaleway server state: running | stopped | stopping | starting | ..."""
        with httpx.Client(timeout=15) as client:
            response = client.get(API.format(zone=zone, server_id=instance_id), headers=self._headers)
            response.raise_for_status()
            return response.json()["server"]["state"]

    def _action(self, zone: str, instance_id: str, action: str) -> None:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                API.format(zone=zone, server_id=instance_id) + "/action",
                headers=self._headers,
                json={"action": action},
            )
            # A poweron on a running server (or poweroff on a stopped one)
            # returns 400 "already ..." — idempotent callers treat that as done.
            if response.status_code == 400 and "already" in response.text:
                return
            response.raise_for_status()


def substrate_from_env() -> ScalewaySubstrate | None:
    """The production wiring: SCW_SECRET_KEY in the chief's environment."""
    key = os.environ.get("SCW_SECRET_KEY", "").strip()
    return ScalewaySubstrate(key) if key else None
