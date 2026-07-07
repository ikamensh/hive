"""Chief discovery for runners: an ordered candidate roster, not an address.

A runner is seeded with one or more chief URLs (`HIVE_URL`, comma-separated)
and learns the rest from the chief itself — every register response carries
the chief's advertised URLs (`HIVE_ADVERTISED_URLS`). The merged roster
persists across restarts, so relocating the chief means starting it with the
new address advertised: the fleet learns it on the next heartbeat and finds
it there once the old address stops answering. The leader lease guarantees
at most one live chief per store, so "first candidate that accepts
registration" is unambiguous.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("hive.worker.roster")


def parse_urls(raw: str) -> list[str]:
    """Split a comma-separated URL list, normalized and de-duplicated."""
    out: list[str] = []
    for part in raw.split(","):
        url = part.strip().rstrip("/")
        if url and url not in out:
            out.append(url)
    return out


class ChiefRoster:
    """Ordered chief candidates: last-known-good first, then configured seeds,
    then chief-advertised URLs. State survives restarts via a JSON file."""

    def __init__(self, seeds: list[str], state_path: Path) -> None:
        self._state_path = state_path
        self._seeds = parse_urls(",".join(seeds))
        self._learned: list[str] = []
        self._preferred: str = ""
        self._lock = threading.RLock()  # main loop + heartbeat thread both touch us
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return  # no state yet, or corrupt: seeds alone still work
        self._learned = parse_urls(",".join(raw.get("learned", [])))
        preferred = str(raw.get("preferred", "")).rstrip("/")
        if preferred in self.candidates():
            self._preferred = preferred

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"preferred": self._preferred, "learned": self._learned})
            )
        except OSError as exc:
            log.warning("could not persist chief roster to %s: %s", self._state_path, exc)

    def candidates(self) -> list[str]:
        """All known chief URLs, most-promising first."""
        with self._lock:
            ordered = [self._preferred] if self._preferred else []
            for url in self._seeds + self._learned:
                if url not in ordered:
                    ordered.append(url)
            return ordered

    def mark_success(self, url: str) -> None:
        """Record the URL a chief actually answered on; it is tried first next time."""
        url = url.rstrip("/")
        with self._lock:
            if url != self._preferred:
                self._preferred = url
                self._save()

    def merge_advertised(self, urls: list[str]) -> None:
        """Fold the chief's advertised URLs into the roster."""
        with self._lock:
            new = [u for u in parse_urls(",".join(urls)) if u not in self.candidates()]
            if new:
                self._learned.extend(new)
                self._save()
