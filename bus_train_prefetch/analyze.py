"""
Compare two snapshots stored by prefetch.py and answer:
    - How much do prices change between two scrape passes?
    - Are most routes stable, or volatile?
    - After N days, what fraction of (route, date) tuples still has the same
      cheapest price within +/-5% tolerance?

Pick "cheapest price per (origin, dest, departure_date, transport_mode)" from
each snapshot, then diff.

Usage:
    uv run bus_train_prefetch/analyze.py                    # compares last two
    uv run bus_train_prefetch/analyze.py 1 3                # compares snapshot 1 and 3
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "prices.db"


def list_snapshots(conn: sqlite3.Connection) -> list[tuple[int, str, str | None]]:
    return list(
        conn.execute(
            "SELECT snapshot_id, started_at, ended_at FROM snapshots_meta ORDER BY snapshot_id"
        )
    )


def cheapest_per_route(conn: sqlite3.Connection, snapshot_id: int) -> dict[tuple, dict]:
    """key = (origin, dest, departure_date, transport_mode) -> {price, company, count}."""
    out: dict[tuple, dict] = {}
    cur = conn.execute(
        "SELECT origin_city, dest_city, departure_date, transport_mode, "
        "MIN(price) AS min_price, COUNT(*) AS n "
        "FROM trips WHERE snapshot_id=? AND price IS NOT NULL "
        "GROUP BY origin_city, dest_city, departure_date, transport_mode",
        (snapshot_id,),
    )
    for o, d, date, mode, p, n in cur:
        out[(o, d, date, mode or "bus")] = {"price": float(p), "n": int(n)}
    return out


def compare(a: dict, b: dict, tol: float = 0.05) -> dict:
    common = set(a.keys()) & set(b.keys())
    only_a = set(a.keys()) - set(b.keys())
    only_b = set(b.keys()) - set(a.keys())

    abs_diffs = []
    rel_diffs = []
    same_within_tol = 0
    cheaper_b = 0
    pricier_b = 0
    for key in common:
        pa, pb = a[key]["price"], b[key]["price"]
        diff = pb - pa
        rel = diff / pa if pa else 0.0
        abs_diffs.append(diff)
        rel_diffs.append(rel)
        if abs(rel) <= tol:
            same_within_tol += 1
        elif diff < 0:
            cheaper_b += 1
        else:
            pricier_b += 1

    def stat(xs):
        if not xs:
            return {}
        return {
            "n": len(xs),
            "mean": statistics.mean(xs),
            "median": statistics.median(xs),
            "stdev": statistics.pstdev(xs),
            "min": min(xs),
            "max": max(xs),
        }

    return {
        "common_routes": len(common),
        "only_in_first": len(only_a),
        "only_in_second": len(only_b),
        "same_within_tol_pct": (same_within_tol / len(common) * 100) if common else 0.0,
        "cheaper_in_second": cheaper_b,
        "pricier_in_second": pricier_b,
        "abs_diff_eur": stat(abs_diffs),
        "rel_diff": stat(rel_diffs),
    }


def fmt(comp: dict, tol: float) -> str:
    lines = [
        f"common (origin,dest,date,mode) keys     : {comp['common_routes']}",
        f"only in snapshot A                      : {comp['only_in_first']}",
        f"only in snapshot B                      : {comp['only_in_second']}",
        f"same cheapest within ±{tol*100:.0f}%               : "
        f"{comp['same_within_tol_pct']:.1f}%",
        f"cheaper in B                            : {comp['cheaper_in_second']}",
        f"pricier in B                            : {comp['pricier_in_second']}",
    ]
    abs_d = comp["abs_diff_eur"]
    rel_d = comp["rel_diff"]
    if abs_d:
        lines.append(
            f"abs diff EUR  median={abs_d['median']:+.2f}  mean={abs_d['mean']:+.2f}  "
            f"stdev={abs_d['stdev']:.2f}  min={abs_d['min']:+.2f}  max={abs_d['max']:+.2f}"
        )
        lines.append(
            f"rel diff      median={rel_d['median']*100:+.1f}%  mean={rel_d['mean']*100:+.1f}%  "
            f"stdev={rel_d['stdev']*100:.1f}%  min={rel_d['min']*100:+.1f}%  "
            f"max={rel_d['max']*100:+.1f}%"
        )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("a", type=int, nargs="?", help="snapshot id A (default: second-most-recent)")
    p.add_argument("b", type=int, nargs="?", help="snapshot id B (default: most recent)")
    p.add_argument("--tol", type=float, default=0.05, help="tolerance for 'same' (default 5%%)")
    p.add_argument("--list", action="store_true", help="just list snapshots and exit")
    args = p.parse_args()

    if not DB_PATH.exists():
        print(f"no database at {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    snaps = list_snapshots(conn)

    if args.list or not snaps:
        for sid, start, end in snaps:
            print(f"  snapshot_id={sid}  started={start}  ended={end or '(unfinished)'}")
        if not snaps:
            print("(no snapshots yet)")
        return 0

    if args.a is None and args.b is None:
        if len(snaps) < 2:
            print("need at least 2 snapshots to compare", file=sys.stderr)
            return 1
        sid_a = snaps[-2][0]
        sid_b = snaps[-1][0]
    else:
        sid_a = args.a
        sid_b = args.b

    a = cheapest_per_route(conn, sid_a)
    b = cheapest_per_route(conn, sid_b)
    print(f"comparing snapshot {sid_a} ({len(a)} keys) vs {sid_b} ({len(b)} keys)")
    print(fmt(compare(a, b, args.tol), args.tol))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
