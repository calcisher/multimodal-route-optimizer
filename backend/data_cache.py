"""Per-data-type cache for flight (SerpAPI) and bus (FlixBus) DataFrames.

Replaces the old endpoint-level cache. Two SQLite tables in
data/search_cache.db, both keyed on (origin, destination, date):

  flight_cache: TTL 6h.   from_iata + to_iata uppercase.
  bus_cache:    TTL 48h.  from_city + to_city normalized lowercase.

DataFrames are stored as JSON via df.to_json/read_json so the cache stays
inspectable. Datetime columns in bus rows are reconstructed on read.

Past-date entries (search date < today) are never served and are pruned
on app startup.
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import date as date_cls, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "search_cache.db"
FLIGHT_TTL_HOURS = 6
BUS_TTL_HOURS = 48
_BUS_DT_COLS = ("departure_dt", "arrival_dt")


def _restore_nones(df: pd.DataFrame) -> pd.DataFrame:
    """pd.read_json revives JSON null as NaN. Downstream code (and Flask's
    JSON encoder) treats Python None and float('nan') very differently —
    most notably, jsonify writes NaN as the literal `NaN`, which JS
    JSON.parse rejects. Coerce NaN/NaT back to None so cache output is
    indistinguishable from fresh data.
    """
    if df.empty:
        return df
    return df.astype(object).where(pd.notna(df), None)


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute("PRAGMA busy_timeout = 30000")
    c.execute("PRAGMA journal_mode = WAL")
    return c


def _init_schema(c: sqlite3.Connection) -> None:
    # cached_at is bound by us as ISO 8601 with 'T' separator (matching
    # _is_fresh / prune cutoffs). Don't rely on CURRENT_TIMESTAMP — its
    # space-separated format breaks the text comparison against our cutoffs.
    c.execute("""
        CREATE TABLE IF NOT EXISTS flight_cache (
            from_iata TEXT NOT NULL,
            to_iata   TEXT NOT NULL,
            date      TEXT NOT NULL,
            payload   TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (from_iata, to_iata, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bus_cache (
            from_city TEXT NOT NULL,
            to_city   TEXT NOT NULL,
            date      TEXT NOT NULL,
            payload   TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (from_city, to_city, date)
        )
    """)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _normalize_city(s: str) -> str:
    """Lowercase + strip diacritics so 'Nürnberg' and 'nurnberg' share a key."""
    if not s:
        return ""
    return (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )


def _normalize_date(d: str) -> str | None:
    """Accepts YYYY-MM-DD or DD.MM.YYYY; returns YYYY-MM-DD or None on failure."""
    if not d:
        return None
    d = d.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _is_past(date_iso: str) -> bool:
    try:
        return date_cls.fromisoformat(date_iso) < date_cls.today()
    except ValueError:
        return False


def _is_fresh(cached_at: str, ttl_hours: int) -> bool:
    try:
        when = datetime.fromisoformat(cached_at.replace(" ", "T"))
    except ValueError:
        return False
    return datetime.now(timezone.utc).replace(tzinfo=None) - when < timedelta(hours=ttl_hours)


# ── flight cache ─────────────────────────────────────────────────────────────

def flight_get(from_iata: str, to_iata: str, date: str) -> pd.DataFrame | None:
    date_iso = _normalize_date(date)
    if not date_iso or _is_past(date_iso):
        return None
    fk = (from_iata or "").upper()
    tk = (to_iata or "").upper()
    if not fk or not tk:
        return None
    try:
        with _conn() as c:
            _init_schema(c)
            row = c.execute(
                "SELECT payload, cached_at FROM flight_cache "
                "WHERE from_iata=? AND to_iata=? AND date=?",
                (fk, tk, date_iso),
            ).fetchone()
        if not row:
            return None
        payload, cached_at = row
        if not _is_fresh(cached_at, FLIGHT_TTL_HOURS):
            return None
        df = pd.read_json(StringIO(payload), orient="records")
        df = _restore_nones(df)
        print(f"💾 flight cache HIT  {fk}→{tk} {date_iso}")
        return df
    except Exception as e:
        print(f"⚠️  flight cache read failed: {e}")
        return None


def flight_set(from_iata: str, to_iata: str, date: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    date_iso = _normalize_date(date)
    if not date_iso or _is_past(date_iso):
        return
    fk = (from_iata or "").upper()
    tk = (to_iata or "").upper()
    if not fk or not tk:
        return
    try:
        payload = df.to_json(orient="records", date_format="iso")
        with _conn() as c:
            _init_schema(c)
            c.execute(
                "INSERT INTO flight_cache (from_iata, to_iata, date, payload, cached_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_iata, to_iata, date) DO UPDATE SET "
                "  payload = excluded.payload, "
                "  cached_at = excluded.cached_at",
                (fk, tk, date_iso, payload, _now_iso()),
            )
        print(f"💾 flight cache SAVE {fk}→{tk} {date_iso} ({len(df)} rows)")
    except Exception as e:
        print(f"⚠️  flight cache write failed: {e}")


# ── bus cache ────────────────────────────────────────────────────────────────

def bus_get(from_city: str, to_city: str, date: str) -> pd.DataFrame | None:
    date_iso = _normalize_date(date)
    if not date_iso or _is_past(date_iso):
        return None
    fk = _normalize_city(from_city)
    tk = _normalize_city(to_city)
    if not fk or not tk:
        return None
    try:
        with _conn() as c:
            _init_schema(c)
            row = c.execute(
                "SELECT payload, cached_at FROM bus_cache "
                "WHERE from_city=? AND to_city=? AND date=?",
                (fk, tk, date_iso),
            ).fetchone()
        if not row:
            return None
        payload, cached_at = row
        if not _is_fresh(cached_at, BUS_TTL_HOURS):
            return None
        df = pd.read_json(StringIO(payload), orient="records")
        df = _restore_nones(df)
        # JSON round-trip drops tz-aware Timestamp dtype — reconstruct.
        for col in _BUS_DT_COLS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        print(f"💾 bus cache HIT    {fk}→{tk} {date_iso}")
        return df
    except Exception as e:
        print(f"⚠️  bus cache read failed: {e}")
        return None


def bus_set(from_city: str, to_city: str, date: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    date_iso = _normalize_date(date)
    if not date_iso or _is_past(date_iso):
        return
    fk = _normalize_city(from_city)
    tk = _normalize_city(to_city)
    if not fk or not tk:
        return
    try:
        payload = df.to_json(orient="records", date_format="iso")
        with _conn() as c:
            _init_schema(c)
            c.execute(
                "INSERT INTO bus_cache (from_city, to_city, date, payload, cached_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_city, to_city, date) DO UPDATE SET "
                "  payload = excluded.payload, "
                "  cached_at = excluded.cached_at",
                (fk, tk, date_iso, payload, _now_iso()),
            )
        print(f"💾 bus cache SAVE   {fk}→{tk} {date_iso} ({len(df)} rows)")
    except Exception as e:
        print(f"⚠️  bus cache write failed: {e}")


# ── maintenance ──────────────────────────────────────────────────────────────

def prune() -> dict:
    """Delete past-date rows and rows whose cached_at is older than their TTL.

    Called from app.py on startup. Also drops the legacy `search_cache` table
    left over from the endpoint-level cache it replaces.
    """
    today = date_cls.today().isoformat()
    flight_cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=FLIGHT_TTL_HOURS)).isoformat()
    bus_cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=BUS_TTL_HOURS)).isoformat()
    stats = {"flight_pruned": 0, "bus_pruned": 0, "legacy_dropped": False}
    try:
        with _conn() as c:
            _init_schema(c)
            r1 = c.execute(
                "DELETE FROM flight_cache WHERE date < ? OR cached_at < ?",
                (today, flight_cutoff),
            )
            stats["flight_pruned"] = r1.rowcount or 0
            r2 = c.execute(
                "DELETE FROM bus_cache WHERE date < ? OR cached_at < ?",
                (today, bus_cutoff),
            )
            stats["bus_pruned"] = r2.rowcount or 0
            existed = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='search_cache'"
            ).fetchone()
            if existed:
                c.execute("DROP TABLE search_cache")
                stats["legacy_dropped"] = True
        print(
            f"🧹 cache prune: flight={stats['flight_pruned']} "
            f"bus={stats['bus_pruned']} "
            f"legacy_dropped={stats['legacy_dropped']}"
        )
    except Exception as e:
        print(f"⚠️  cache prune failed: {e}")
    return stats
