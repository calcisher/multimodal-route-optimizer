"""
Pre-fetch bus + train prices for IT+DE city pairs within 300 km, for the
upcoming week (next Sunday .. following Saturday), and persist every result
into SQLite so the UI can serve cached prices instead of waiting for slow
checkmybus.com scrapes.

Each script invocation creates a new "snapshot" so we can later compare two
runs and answer:
    - How much do prices drift over a week?
    - How long can we keep cached results before they go stale?

Usage:
    uv run bus_train_prefetch/prefetch.py
    uv run bus_train_prefetch/prefetch.py --dry-run        # just print plan
    uv run bus_train_prefetch/prefetch.py --resume          # resume latest snapshot
    uv run bus_train_prefetch/prefetch.py --threshold 250  # custom km cutoff
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

# checkmybus.py is in the parent directory
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from checkmybus import CheckMyBusClient, CheckMyBusSearchParams  # noqa: E402

HERE = Path(__file__).resolve().parent
AIRPORTS_JSON = ROOT / "filtered_airports_it_de.json"
DB_PATH = HERE / "prices.db"
LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_THRESHOLD_KM = 300
DEFAULT_INTERVAL_SEC = 10
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SEC = 30
DAYS_IN_WEEK = 7

run_log = logging.getLogger("prefetch")
run_log.setLevel(logging.INFO)
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
run_log.addHandler(_console)
_file = logging.FileHandler(LOG_DIR / "run.log")
_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
run_log.addHandler(_file)

failed_log_path = LOG_DIR / "failed.jsonl"


def load_cities(path: Path) -> dict[str, tuple[float, float, str]]:
    with path.open() as f:
        data = json.load(f)
    cities: dict[str, tuple[float, float, str]] = {}
    for v in data.values():
        c = (v.get("city") or "").strip()
        if not c:
            continue
        if c in cities:
            continue
        cities[c] = (float(v["lat"]), float(v["lon"]), v["country"])
    return cities


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def build_pairs(cities: dict[str, tuple[float, float, str]], threshold_km: float):
    names = list(cities.keys())
    ordered: list[tuple[str, str, float]] = []
    for i, j in combinations(range(len(names)), 2):
        a, b = names[i], names[j]
        d = haversine_km(cities[a][:2], cities[b][:2])
        if d <= threshold_km:
            ordered.append((a, b, d))
            ordered.append((b, a, d))
    ordered.sort(key=lambda p: (p[2], p[0], p[1]))
    return ordered


def next_sunday(today: datetime | None = None) -> datetime:
    today = today or datetime.now()
    # Monday=0 .. Sunday=6
    days_ahead = (6 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)


def week_dates(start: datetime, count: int = DAYS_IN_WEEK) -> list[str]:
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count)]


def init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshots_meta (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL,
            ended_at    TEXT,
            threshold_km REAL,
            week_start  TEXT,
            note        TEXT
        );
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            queried_at  TEXT NOT NULL,
            origin_city TEXT NOT NULL,
            dest_city   TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            trip_id     TEXT,
            company     TEXT,
            operator    TEXT,
            origin_station TEXT,
            dest_station   TEXT,
            departure_dt   TEXT,
            arrival_dt     TEXT,
            duration_min   INTEGER,
            price          REAL,
            currency       TEXT,
            transport_mode TEXT,
            stops          INTEGER,
            free_seats     INTEGER,
            deep_link      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_trips_route
            ON trips(origin_city, dest_city, departure_date);
        CREATE INDEX IF NOT EXISTS idx_trips_snapshot ON trips(snapshot_id);
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            queried_at  TEXT NOT NULL,
            origin_city TEXT NOT NULL,
            dest_city   TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            status      TEXT NOT NULL,    -- ok / empty / failed
            attempts    INTEGER NOT NULL,
            trip_count  INTEGER,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_query_log_snapshot
            ON query_log(snapshot_id, origin_city, dest_city, departure_date);
        """
    )
    conn.commit()
    return conn


def begin_snapshot(conn: sqlite3.Connection, threshold_km: float, week_start: str) -> int:
    cur = conn.execute(
        "INSERT INTO snapshots_meta (started_at, threshold_km, week_start) VALUES (?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), threshold_km, week_start),
    )
    conn.commit()
    return int(cur.lastrowid)


def end_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> None:
    conn.execute(
        "UPDATE snapshots_meta SET ended_at=? WHERE snapshot_id=?",
        (datetime.now(timezone.utc).isoformat(), snapshot_id),
    )
    conn.commit()


def already_done(conn: sqlite3.Connection, snapshot_id: int, origin: str, dest: str, date: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM query_log WHERE snapshot_id=? AND origin_city=? AND dest_city=? "
        "AND departure_date=? AND status IN ('ok','empty') LIMIT 1",
        (snapshot_id, origin, dest, date),
    )
    return cur.fetchone() is not None


def latest_snapshot(conn: sqlite3.Connection) -> int | None:
    cur = conn.execute(
        "SELECT snapshot_id FROM snapshots_meta WHERE ended_at IS NULL "
        "ORDER BY snapshot_id DESC LIMIT 1"
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def store_trips(conn, snapshot_id, queried_at, origin, dest, date, result):
    rows = []
    for t in result.trips:
        rows.append(
            (
                snapshot_id,
                queried_at,
                origin,
                dest,
                date,
                t.trip_id,
                t.company,
                t.operator,
                t.origin_station,
                t.dest_station,
                t.departure_dt.isoformat() if t.departure_dt else None,
                t.arrival_dt.isoformat() if t.arrival_dt else None,
                t.duration_min,
                t.price,
                t.currency,
                t.transport_mode,
                t.stops,
                t.free_seats,
                t.deep_link,
            )
        )
    if rows:
        conn.executemany(
            "INSERT INTO trips (snapshot_id, queried_at, origin_city, dest_city, "
            "departure_date, trip_id, company, operator, origin_station, dest_station, "
            "departure_dt, arrival_dt, duration_min, price, currency, transport_mode, "
            "stops, free_seats, deep_link) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    conn.commit()


def log_query(conn, snapshot_id, queried_at, origin, dest, date, status, attempts, trip_count, error):
    conn.execute(
        "INSERT INTO query_log (snapshot_id, queried_at, origin_city, dest_city, "
        "departure_date, status, attempts, trip_count, error) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (snapshot_id, queried_at, origin, dest, date, status, attempts, trip_count, error),
    )
    conn.commit()


def fetch_with_retry(client: CheckMyBusClient, origin: str, dest: str, date: str):
    """Returns (result, attempts, error_str_or_None). result.trips may be empty even on success."""
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            res = client.search(
                CheckMyBusSearchParams(
                    departure_location=origin,
                    arrival_location=dest,
                    departure_date=date,
                )
            )
            if res.trips:
                return res, attempt, None
            last_error = "empty_result"
        except Exception as e:  # noqa: BLE001
            last_error = f"{type(e).__name__}: {e}"
            run_log.warning("attempt %d %s->%s %s failed: %s", attempt, origin, dest, date, last_error)
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SEC)
    return None, RETRY_ATTEMPTS, last_error


def append_failed(origin, dest, date, error):
    with failed_log_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "origin": origin,
                    "dest": dest,
                    "date": date,
                    "error": error,
                }
            )
            + "\n"
        )


def estimate_runtime(n_queries: int, interval: int) -> str:
    base = n_queries * interval
    fail = n_queries * 0.05 * RETRY_ATTEMPTS * RETRY_BACKOFF_SEC
    return (
        f"queries={n_queries}  "
        f"base={base/3600:.2f}h  "
        f"with-5%-retries={(base+fail)/3600:.2f}h "
        f"({(base+fail)/86400:.2f} days)"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_KM)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC,
                        help="seconds between successful queries (default 10)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="resume the most recent unfinished snapshot")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N queries (smoke test)")
    args = parser.parse_args()

    cities = load_cities(AIRPORTS_JSON)
    pairs = build_pairs(cities, args.threshold)
    week_start = next_sunday()
    dates = week_dates(week_start)
    n_queries = len(pairs) * len(dates)

    print(f"cities={len(cities)}  pairs(ordered)={len(pairs)}  dates={len(dates)} "
          f"({dates[0]}..{dates[-1]})")
    print(estimate_runtime(n_queries, args.interval))

    if args.dry_run:
        for o, d, km in pairs[:20]:
            print(f"  {o} -> {d}  {km:.0f} km")
        if len(pairs) > 20:
            print(f"  ... +{len(pairs)-20} more pairs")
        return 0

    conn = init_db(DB_PATH)

    if args.resume:
        snapshot_id = latest_snapshot(conn)
        if snapshot_id is None:
            run_log.info("no unfinished snapshot to resume; starting new one")
            snapshot_id = begin_snapshot(conn, args.threshold, week_start.strftime("%Y-%m-%d"))
        else:
            run_log.info("resuming snapshot_id=%d", snapshot_id)
    else:
        snapshot_id = begin_snapshot(conn, args.threshold, week_start.strftime("%Y-%m-%d"))
        run_log.info("started snapshot_id=%d", snapshot_id)

    client = CheckMyBusClient()
    try:
        done_count = 0
        ok_count = 0
        empty_count = 0
        fail_count = 0
        skipped_count = 0
        t0 = time.time()
        for date in dates:
            for origin, dest, km in pairs:
                done_count += 1
                if args.limit and done_count > args.limit:
                    break
                if args.resume and already_done(conn, snapshot_id, origin, dest, date):
                    skipped_count += 1
                    continue

                queried_at = datetime.now(timezone.utc).isoformat()
                t_start = time.time()
                result, attempts, err = fetch_with_retry(client, origin, dest, date)

                if result is not None and result.trips:
                    store_trips(conn, snapshot_id, queried_at, origin, dest, date, result)
                    log_query(conn, snapshot_id, queried_at, origin, dest, date,
                              "ok", attempts, len(result.trips), None)
                    ok_count += 1
                elif result is not None and not result.trips and err is None:
                    log_query(conn, snapshot_id, queried_at, origin, dest, date,
                              "empty", attempts, 0, None)
                    empty_count += 1
                else:
                    log_query(conn, snapshot_id, queried_at, origin, dest, date,
                              "failed", attempts, 0, err or "unknown")
                    append_failed(origin, dest, date, err)
                    fail_count += 1

                if done_count % 50 == 0 or done_count == 1:
                    elapsed = time.time() - t0
                    rate = done_count / max(elapsed, 1e-6)
                    eta = (n_queries - done_count) / max(rate, 1e-9)
                    run_log.info(
                        "progress %d/%d  ok=%d empty=%d fail=%d skip=%d  "
                        "rate=%.2f q/s  eta=%.1fh",
                        done_count, n_queries, ok_count, empty_count, fail_count,
                        skipped_count, rate, eta / 3600,
                    )

                # rate limit: aim for one query every `interval` seconds total
                spent = time.time() - t_start
                sleep_for = max(0.0, args.interval - spent)
                time.sleep(sleep_for)
            if args.limit and done_count > args.limit:
                break

        end_snapshot(conn, snapshot_id)
        run_log.info(
            "done snapshot_id=%d  ok=%d empty=%d fail=%d skip=%d total=%d elapsed=%.1fh",
            snapshot_id, ok_count, empty_count, fail_count, skipped_count, done_count,
            (time.time() - t0) / 3600,
        )
    finally:
        client.close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
