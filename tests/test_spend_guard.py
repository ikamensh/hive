"""Scaleway spend killswitch: the guard that stops hive burning the monthly credits.

Properties verified:
- money folding is exact and signed — discount lines (negative nanos) subtract,
  and per-project shares sum back to the org total;
- the threshold bands are monotone in spend and inclusive at each boundary, so a
  spend that trips once never un-trips as it grows;
- scoping to a project counts only that project's lines, and an unknown project
  reads as zero rather than falling back to the org total (a typo'd --scope must
  not silently guard nothing... and must not silently guard everything either);
- --dry-run performs no state-changing call, while a real trip powers off only
  *running* instances and deletes the key last — deleting it first would revoke
  the credentials the poweroffs need;
- the exit code encodes the band, so cron/systemd can alert off it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Loaded by path, not imported: the killswitch is deliberately a standalone stdlib
# script so it still runs when the hive package is broken or absent.
spec = importlib.util.spec_from_file_location("spend_guard", Path(__file__).parent.parent / "deploy" / "spend_guard.py")
spend_guard = importlib.util.module_from_spec(spec)
sys.modules["spend_guard"] = spend_guard  # @dataclass resolves annotations via sys.modules
spec.loader.exec_module(spend_guard)


def money(amount: float) -> dict:
    """EUR as Scaleway's units+nanos pair."""
    units = int(amount)
    return {"units": units, "nanos": round((amount - units) * 1e9)}


def consumption(**projects: float) -> dict:
    return {
        "consumptions": [{"value": money(v), "project_name": k} for k, v in projects.items()],
        "updated_at": "2026-08-12T08:18:21Z",
    }


class FakeScaleway:
    """Records every call so tests can assert on what was *not* done."""

    def __init__(self, payload: dict, servers: dict[str, list[dict]] | None = None) -> None:
        self.payload = payload
        self._servers = servers or {}
        self.powered_off: list[str] = []
        self.deleted: list[str] = []
        self.calls: list[str] = []

    def consumptions(self, organization_id):
        return self.payload

    def servers(self, zone, project):
        return self._servers.get(zone, [])

    def poweroff(self, zone, server_id):
        assert not self.deleted, "key deleted before poweroff — credentials are gone"
        self.powered_off.append(server_id)
        self.calls.append(f"poweroff:{server_id}")

    def delete_api_key(self, access_key):
        self.deleted.append(access_key)
        self.calls.append(f"delete:{access_key}")


def test_money_folding_is_signed_and_exact():
    """A discount line subtracts; it does not read as a positive charge."""
    spend = spend_guard.read_spend(
        {"consumptions": [
            {"value": {"units": 11, "nanos": 760000000}, "project_name": "hive"},
            {"value": {"units": 0, "nanos": -800000000}, "project_name": "hive"},
        ]}
    )
    assert spend.total == pytest.approx(10.96)


def test_project_shares_sum_to_the_org_total():
    spend = spend_guard.read_spend(consumption(hive=12.80, covenance=39.69))
    assert sum(spend.by_project.values()) == pytest.approx(spend.total)
    assert spend.scoped(None) == pytest.approx(52.49)


def test_bands_are_monotone_and_inclusive():
    order = {"ok": 0, "warn": 1, "kill": 2}
    previous = 0
    for amount in [0, 500, 799.99, 800, 900, 999.99, 1000, 1000.01, 5000]:
        band = order[spend_guard.verdict(amount, 800, 1000)]
        assert band >= previous, f"spend {amount} de-escalated the verdict"
        previous = band
    assert spend_guard.verdict(800, 800, 1000) == "warn"
    assert spend_guard.verdict(1000, 800, 1000) == "kill"


def test_unknown_scope_reads_zero_not_the_org_total():
    """A mistyped --scope must fail open on the guard, never guard the wrong number."""
    spend = spend_guard.read_spend(consumption(hive=12.80, covenance=39.69))
    assert spend.scoped("hvie") == 0.0
    assert spend.scoped("hive") == pytest.approx(12.80)


def test_dry_run_changes_nothing():
    scw = FakeScaleway({}, {"fr-par-1": [{"id": "s1", "name": "hive-vm", "commercial_type": "PLAY2-NANO", "state": "running"}]})
    actions = spend_guard.trip(scw, "proj", "SCWKEY", dry_run=True)
    assert scw.calls == []
    assert any("poweroff" in a for a in actions) and any("delete" in a for a in actions)


def test_trip_stops_running_instances_then_deletes_the_key():
    scw = FakeScaleway({}, {"fr-par-1": [
        {"id": "running-1", "name": "hive-vm", "commercial_type": "PLAY2-NANO", "state": "running"},
        {"id": "stopped-1", "name": "hive-droid", "commercial_type": "PLAY2-MICRO", "state": "stopped"},
    ]})
    spend_guard.trip(scw, "proj", "SCWKEY", dry_run=False)
    assert scw.powered_off == ["running-1"], "stopped instances bill storage only; leave them"
    assert scw.calls[-1] == "delete:SCWKEY", "key must die last, after the poweroffs it authorizes"


@pytest.mark.parametrize("spend_eur,expected_exit", [(10.0, 0), (850.0, 1), (1200.0, 2)])
def test_exit_code_encodes_the_band(monkeypatch, spend_eur, expected_exit):
    scw = FakeScaleway(consumption(hive=spend_eur))
    monkeypatch.setattr(spend_guard, "find_token", lambda: "token")
    monkeypatch.setattr(spend_guard, "Scaleway", lambda token: scw)
    assert spend_guard.main(["--dry-run"]) == expected_exit
