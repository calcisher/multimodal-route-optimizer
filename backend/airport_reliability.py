"""Airport reliability / circuit-breaker tracker.

Keeps a per-IATA strike count of empty SerpAPI flight results. Once an
airport hits the strike threshold, we suspend it for a few hours so the
nearby-airport pipeline stops burning credits on a hub that has no
service worth pulling. A successful query forgives one strike and lifts
suspension.

State lives in `airport_reliability.json` next to this file — separate
from the master airport JSON (`filtered_airports_it_de.json`) on
purpose, so a `git pull` of the master list doesn't clobber per-machine
reliability data.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_FILE = Path(__file__).parent.parent / "data" / "airport_reliability.json"
_LOCK = threading.Lock()

STRIKE_THRESHOLD = 3
SUSPENSION_HOURS = 6
_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict:
    if not _FILE.exists():
        return {"version": _VERSION, "airports": {}}
    try:
        data = json.loads(_FILE.read_text())
        if data.get("version") != _VERSION or "airports" not in data:
            return {"version": _VERSION, "airports": {}}
        return data
    except (OSError, json.JSONDecodeError):
        return {"version": _VERSION, "airports": {}}


def _save(data: dict) -> None:
    data["updated_at"] = _now().isoformat()
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, _FILE)


def _entry(data: dict, iata: str) -> dict:
    return data["airports"].setdefault(
        iata,
        {
            "strikes": 0,
            "suspended_until": None,
            "last_empty_at": None,
            "last_success_at": None,
        },
    )


def is_suspended(iata: str) -> bool:
    if not iata:
        return False
    iata = iata.upper()
    with _LOCK:
        data = _load()
        e = data["airports"].get(iata)
        if not e or not e.get("suspended_until"):
            return False
        try:
            until = datetime.fromisoformat(e["suspended_until"])
        except ValueError:
            return False
        return _now() < until


def record_empty(iata: str) -> None:
    """One more strike. Suspends after STRIKE_THRESHOLD strikes."""
    if not iata:
        return
    iata = iata.upper()
    with _LOCK:
        data = _load()
        e = _entry(data, iata)
        e["strikes"] = int(e.get("strikes", 0)) + 1
        e["last_empty_at"] = _now().isoformat()
        if e["strikes"] >= STRIKE_THRESHOLD:
            until = _now() + timedelta(hours=SUSPENSION_HOURS)
            e["suspended_until"] = until.isoformat()
            print(f"   ⏸  {iata} suspended until {until.isoformat()} ({e['strikes']} strikes)")
        _save(data)


def record_success(iata: str) -> None:
    """Forgive one strike, lift suspension."""
    if not iata:
        return
    iata = iata.upper()
    with _LOCK:
        data = _load()
        e = _entry(data, iata)
        e["strikes"] = max(0, int(e.get("strikes", 0)) - 1)
        e["suspended_until"] = None
        e["last_success_at"] = _now().isoformat()
        _save(data)


def filter_suspended(iatas: list[str]) -> tuple[list[str], list[str]]:
    """Return (active, suspended) split of `iatas`."""
    active, suspended = [], []
    for c in iatas:
        (suspended if is_suspended(c) else active).append(c)
    return active, suspended


def status_snapshot() -> dict:
    with _LOCK:
        return _load()
