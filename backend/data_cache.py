"""Per-data-type cache for flight (SerpAPI), bus (FlixBus) and train DataFrames.

Three SQLite tables in data/search_cache.db:

  flight_cache: TTL 6h.  IATA codes (uppercase). IATA is canonical, no
                aliasing problem.
  bus_cache:    TTL 1h.  FlixBus city *UUIDs* (not free-text names).
                FlixBus has multiple stops per metro ("Frankfurt" vs
                "Frankfurt Airport"), and the upstream city name we
                receive can vary across SerpAPI / geocoder paths. Keying
                off the resolved UUID is the only collision-free option.
  train_cache: TTL 6h.  Normalized city names (lowercase + diacritic-stripped).
                Train providers (DB / Trenitalia) don't expose UUIDs, so we
                normalize the inputs to make 'Nürnberg' and 'nurnberg' share
                a key.

Reads return (df, cached_at) so the UI can show data freshness.

Past-date entries (search date < today) are never served and are pruned
on app startup. Stale rows past TTL are also pruned.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import unicodedata
from datetime import date as date_cls, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "search_cache.db"
FLIGHT_TTL_HOURS = 6
BUS_TTL_HOURS = 1
TRAIN_TTL_HOURS = 6
_BUS_DT_COLS = ("departure_dt", "arrival_dt")
_TRAIN_DT_COLS = ("departure_dt", "arrival_dt")


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
    # synchronous=NORMAL is the recommended pairing with WAL: durable
    # against process crashes, only loses the last txn on power loss
    # (acceptable for a cache). FULL is overkill here.
    c.execute("PRAGMA synchronous = NORMAL")
    return c


def _bus_cache_columns(c: sqlite3.Connection) -> set[str]:
    rows = c.execute("PRAGMA table_info(bus_cache)").fetchall()
    return {r[1] for r in rows}


def _drop_legacy_bus_cache(c: sqlite3.Connection) -> None:
    """Old bus_cache used (from_city, to_city) free-text keys, which
    fragmented the same FlixBus stop across multiple variants
    ('frankfurt', 'frankfurt am main', 'frankfurt-am-main'). The new
    schema is UUID-keyed and incompatible. If we detect the old shape,
    drop it — the data was per-hour-stale anyway.
    """
    cols = _bus_cache_columns(c)
    if cols and "from_city_id" not in cols:
        c.execute("DROP TABLE IF EXISTS bus_cache")


def _init_schema(c: sqlite3.Connection) -> None:
    _drop_legacy_bus_cache(c)
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
            from_city_id TEXT NOT NULL,
            to_city_id   TEXT NOT NULL,
            date         TEXT NOT NULL,
            payload      TEXT NOT NULL,
            cached_at    TEXT NOT NULL,
            PRIMARY KEY (from_city_id, to_city_id, date)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS train_cache (
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


def _normalize_city(s: str) -> str:
    """Lowercase + strip diacritics so 'Nürnberg' and 'nurnberg' share a key."""
    if not s:
        return ""
    return (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
        .lower()
    )


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

def flight_get(from_iata: str, to_iata: str, date: str) -> tuple[pd.DataFrame, str] | None:
    """Returns (df, cached_at_iso) on hit, None on miss/stale."""
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
        print(f"💾 flight cache HIT  {fk}→{tk} {date_iso} (cached {cached_at})")
        return df, cached_at
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

def bus_get(from_city_id: str, to_city_id: str, date: str) -> tuple[pd.DataFrame, str] | None:
    """Returns (df, cached_at_iso) on hit, None on miss/stale.

    Keyed on FlixBus city UUIDs — NOT free-text names. Resolve the IDs
    via flixbus_finder.find_city before calling.
    """
    date_iso = _normalize_date(date)
    if not date_iso or _is_past(date_iso):
        return None
    fk = (from_city_id or "").strip()
    tk = (to_city_id or "").strip()
    if not fk or not tk:
        return None
    try:
        with _conn() as c:
            _init_schema(c)
            row = c.execute(
                "SELECT payload, cached_at FROM bus_cache "
                "WHERE from_city_id=? AND to_city_id=? AND date=?",
                (fk, tk, date_iso),
            ).fetchone()
        if not row:
            return None
        payload, cached_at = row
        if not _is_fresh(cached_at, BUS_TTL_HOURS):
            return None
        df = pd.read_json(StringIO(payload), orient="records")
        df = _restore_nones(df)
        # JSON round-trip drops dtype — reconstruct (rows are written
        # tz-naive by flixbus_finder.get_trips, so no TZ shift here).
        for col in _BUS_DT_COLS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        print(f"💾 bus cache HIT    {fk[:8]}…→{tk[:8]}… {date_iso} (cached {cached_at})")
        return df, cached_at
    except Exception as e:
        print(f"⚠️  bus cache read failed: {e}")
        return None


def bus_set(from_city_id: str, to_city_id: str, date: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    date_iso = _normalize_date(date)
    if not date_iso or _is_past(date_iso):
        return
    fk = (from_city_id or "").strip()
    tk = (to_city_id or "").strip()
    if not fk or not tk:
        return
    try:
        payload = df.to_json(orient="records", date_format="iso")
        with _conn() as c:
            _init_schema(c)
            c.execute(
                "INSERT INTO bus_cache (from_city_id, to_city_id, date, payload, cached_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_city_id, to_city_id, date) DO UPDATE SET "
                "  payload = excluded.payload, "
                "  cached_at = excluded.cached_at",
                (fk, tk, date_iso, payload, _now_iso()),
            )
        print(f"💾 bus cache SAVE   {fk[:8]}…→{tk[:8]}… {date_iso} ({len(df)} rows)")
    except Exception as e:
        print(f"⚠️  bus cache write failed: {e}")


# ── train cache ──────────────────────────────────────────────────────────────

def train_get(from_city: str, to_city: str, date: str) -> tuple[pd.DataFrame, str] | None:
    """Returns (df, cached_at_iso) on hit, None on miss/stale.

    Keyed on normalized city names (lowercase + diacritic-stripped).
    """
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
                "SELECT payload, cached_at FROM train_cache "
                "WHERE from_city=? AND to_city=? AND date=?",
                (fk, tk, date_iso),
            ).fetchone()
        if not row:
            return None
        payload, cached_at = row
        if not _is_fresh(cached_at, TRAIN_TTL_HOURS):
            return None
        df = pd.read_json(StringIO(payload), orient="records")
        df = _restore_nones(df)
        for col in _TRAIN_DT_COLS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        print(f"💾 train cache HIT  {fk}→{tk} {date_iso} (cached {cached_at})")
        return df, cached_at
    except Exception as e:
        print(f"⚠️  train cache read failed: {e}")
        return None


def train_set(from_city: str, to_city: str, date: str, df: pd.DataFrame) -> None:
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
                "INSERT INTO train_cache (from_city, to_city, date, payload, cached_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_city, to_city, date) DO UPDATE SET "
                "  payload = excluded.payload, "
                "  cached_at = excluded.cached_at",
                (fk, tk, date_iso, payload, _now_iso()),
            )
        print(f"💾 train cache SAVE {fk}→{tk} {date_iso} ({len(df)} rows)")
    except Exception as e:
        print(f"⚠️  train cache write failed: {e}")


# ── maintenance ──────────────────────────────────────────────────────────────

def prune() -> dict:
    """Delete past-date rows and rows whose cached_at is older than their TTL.

    Called from app.py on startup. Also drops the legacy `search_cache` table
    left over from the endpoint-level cache it replaces.
    """
    today = date_cls.today().isoformat()
    flight_cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=FLIGHT_TTL_HOURS)).isoformat()
    bus_cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=BUS_TTL_HOURS)).isoformat()
    train_cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=TRAIN_TTL_HOURS)).isoformat()
    stats = {"flight_pruned": 0, "bus_pruned": 0, "train_pruned": 0, "legacy_dropped": False}
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
            r3 = c.execute(
                "DELETE FROM train_cache WHERE date < ? OR cached_at < ?",
                (today, train_cutoff),
            )
            stats["train_pruned"] = r3.rowcount or 0
            existed = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='search_cache'"
            ).fetchone()
            if existed:
                c.execute("DROP TABLE search_cache")
                stats["legacy_dropped"] = True
        print(
            f"🧹 cache prune: flight={stats['flight_pruned']} "
            f"bus={stats['bus_pruned']} "
            f"train={stats['train_pruned']} "
            f"legacy_dropped={stats['legacy_dropped']}"
        )
    except Exception as e:
        print(f"⚠️  cache prune failed: {e}")
    return stats


_prune_thread: threading.Thread | None = None
_prune_lock = threading.Lock()


def start_periodic_prune(interval_minutes: int = 30) -> threading.Thread:
    """Run prune() forever in a daemon thread, every interval_minutes.

    Idempotent — calling twice (e.g. under Flask's reloader) reuses the
    existing thread instead of spawning a second one. Daemon=True so the
    thread doesn't block process shutdown.
    """
    global _prune_thread
    with _prune_lock:
        if _prune_thread is not None and _prune_thread.is_alive():
            return _prune_thread

        def _loop() -> None:
            while True:
                time.sleep(interval_minutes * 60)
                try:
                    prune()
                except Exception as e:
                    print(f"⚠️  periodic prune crashed: {e}")

        _prune_thread = threading.Thread(target=_loop, name="cache-prune", daemon=True)
        _prune_thread.start()
        print(f"🧹 cache prune scheduled every {interval_minutes} min")
        return _prune_thread
