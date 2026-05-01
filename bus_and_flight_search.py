"""Ground (bus) → flight pipeline, hub-grouped.

Pipeline: take the user's departure city, find airports within
max_distance_km using filtered_airports_it_de.json, keep the closest
`limit` of them, and for each pull SerpAPI's top 3 "best" + top 3 "other"
flights to the target plus all FlixBus options from the departure city
to that hub city.

Returns one dict per hub with `bus_options[]` and `flight_options[]`
attached raw — the cross-product (which bus pairs with which flight) is
deferred to the frontend so users can pick the pairing themselves.

The 2-hour minimum transfer rule is preserved in `min_total_price` (the
cheapest pair that meets the rule), so hubs can still be sorted/filtered
by realistic minimums even though no combos are pre-computed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd

from airport_reliability import is_suspended
from flight_and_ground_search import (
    COUNTRY_EN,
    airports_df,
    geocode_city,
    haversine_km,
)
from flight_search import flight_search
from flixbus_finder import get_trips


def find_nearby_airports(
    departure_city: str,
    max_distance_km: int = 300,
    limit: int = 5,
) -> pd.DataFrame:
    """Closest `limit` airports within max_distance_km of departure_city."""
    lat, lon = geocode_city(departure_city)

    rows = []
    for _, r in airports_df.iterrows():
        d = haversine_km(lat, lon, r["lat"], r["lon"])
        if d <= max_distance_km:
            country_en = COUNTRY_EN.get(r["country"], r["country"])
            rows.append({
                "iata": r["iata"],
                "city": r["city"],
                "country": r["country"],
                "lat": r["lat"],
                "lon": r["lon"],
                "distance_km": round(d, 1),
                "bus_arrival_name": f'{r["city"]}, {country_en}',
            })

    if not rows:
        return pd.DataFrame()

    return (
        pd.DataFrame(rows)
        .sort_values("distance_km")
        .head(limit)
        .reset_index(drop=True)
    )


_TIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")
_CLOCK_FORMATS = ("%H:%M", "%I:%M %p", "%I:%M%p")


def _to_naive(dt, default_date: str | None = None) -> datetime | None:
    """Normalize anything we get (str / pd.Timestamp / datetime / None / NaT)
    to a tz-naive datetime so we can compare bus arrivals to flight
    departures without tripping over mixed offsets.

    `default_date` (YYYY-MM-DD) expands clock-only strings like "08:45"
    into a full datetime — SerpAPI sometimes returns flight times that
    way and we need a real date to enforce the 2-hour rule.
    """
    if dt is None:
        return None
    try:
        if pd.isna(dt):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(dt, pd.Timestamp):
        # tz_localize(None) strips the offset while preserving the wall-clock
        # time. We use this (rather than tz_convert) because flight times come
        # from SerpAPI as naive local strings — converting bus times to UTC
        # would put the two timelines hours apart and break the 2h connection
        # rule.
        ts = dt.tz_localize(None) if dt.tzinfo else dt
        return ts.to_pydatetime()
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    s = str(dt).strip()
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    if default_date:
        try:
            base = datetime.strptime(default_date, "%Y-%m-%d").date()
        except ValueError:
            base = None
        if base is not None:
            for fmt in _CLOCK_FORMATS:
                try:
                    t = datetime.strptime(s, fmt).time()
                    return datetime.combine(base, t)
                except ValueError:
                    continue
    return None


def _bus_option(idx: int, row: pd.Series) -> dict:
    return {
        "id": f"b{idx}",
        "price_eur": float(row["price_eur"]) if pd.notna(row["price_eur"]) else None,
        "duration_min": int(row["duration_min"]) if pd.notna(row.get("duration_min")) else None,
        "departure_dt": row.get("departure_dt"),
        "arrival_dt": row.get("arrival_dt"),
        "origin": row.get("origin"),
        "destination": row.get("destination"),
        "url": row.get("url"),
    }


def _flight_option(idx: int, row: pd.Series, hub_iata: str, target_iata: str) -> dict:
    legs = row.get("legs") if "legs" in row.index else None
    legs = legs if isinstance(legs, list) else []
    layovers = row.get("layovers") if "layovers" in row.index else None
    layovers = layovers if isinstance(layovers, list) else []
    price = row.get("price")
    return {
        "id": f"f{idx}",
        "type": row.get("flight_type", ""),
        "airline": row.get("airline", ""),
        "flight_number": row.get("flight_number", ""),
        "price_eur": float(price) if pd.notna(price) else None,
        "duration_min": int(row["duration"]) if pd.notna(row.get("duration")) else None,
        "stops": int(row["stops"]) if pd.notna(row.get("stops")) else 0,
        "departure_time": row.get("departure_time"),
        "arrival_time": row.get("arrival_time"),
        "departure_iata": row.get("departure_iata") or hub_iata,
        "arrival_iata": row.get("arrival_iata") or target_iata,
        "legs": legs,
        "layovers": layovers,
        "link": row.get("link"),
    }


def _min_valid_total(
    bus_options: list[dict],
    flight_options: list[dict],
    outbound_date: str,
    min_transfer_hours: float,
) -> float | None:
    """Cheapest bus_price + flight_price across pairs where the bus lands
    at least `min_transfer_hours` before takeoff. None if no pair valid.
    """
    threshold = timedelta(hours=min_transfer_hours)
    best: float | None = None
    for f in flight_options:
        if f["price_eur"] is None:
            continue
        f_dep = _to_naive(f.get("departure_time"), default_date=outbound_date)
        if f_dep is None:
            continue
        deadline = f_dep - threshold
        for b in bus_options:
            if b["price_eur"] is None:
                continue
            b_arr = _to_naive(b.get("arrival_dt"), default_date=outbound_date)
            if b_arr is None or b_arr > deadline:
                continue
            total = b["price_eur"] + f["price_eur"]
            if best is None or total < best:
                best = total
    return best


def _fetch_hub(
    ap: pd.Series,
    arrival_id: str,
    outbound_date: str,
    ground_date: str,
    dep_query: str,
    flight_cap_best: int,
    flight_cap_other: int,
    min_transfer_hours: float,
) -> dict | None:
    """Pull flights + buses for one hub. Returns hub dict or None if either side failed/empty."""
    try:
        flights_df = flight_search(ap["iata"], arrival_id, outbound_date,
                                   track_iata=ap["iata"])
    except Exception as e:
        print(f"   flight_search({ap['iata']} → {arrival_id}) raised: {e}")
        return None
    if flights_df is None or flights_df.empty or "flight_type" not in flights_df.columns:
        return None

    best_subset = flights_df[flights_df["flight_type"] == "Best"].head(flight_cap_best)
    other_subset = flights_df[flights_df["flight_type"] == "Other"].head(flight_cap_other)
    flights_df = pd.concat([best_subset, other_subset], ignore_index=True)
    flights_df = flights_df[flights_df["price"].notna()]
    if flights_df.empty:
        return None

    try:
        ground_df = get_trips(dep_query, ap["city"], ground_date)
    except Exception as e:
        print(f"   get_trips({dep_query} → {ap['city']}) raised: {e}")
        return None
    if ground_df is None or ground_df.empty:
        return None

    ground_df = ground_df.copy()
    ground_df["_arr_naive"] = ground_df["arrival_dt"].map(_to_naive)
    ground_df = ground_df[
        ground_df["_arr_naive"].notna() & ground_df["price_eur"].notna()
    ].reset_index(drop=True)
    if ground_df.empty:
        return None

    flight_options = [
        _flight_option(i, r, ap["iata"], arrival_id)
        for i, (_, r) in enumerate(flights_df.iterrows())
    ]
    bus_options = [
        _bus_option(i, r) for i, (_, r) in enumerate(ground_df.iterrows())
    ]
    min_total = _min_valid_total(
        bus_options, flight_options, outbound_date, min_transfer_hours
    )

    return {
        "hub": {
            "iata": ap["iata"],
            "city": ap["city"],
            "country": ap["country"],
            "country_en": COUNTRY_EN.get(ap["country"], ap["country"]),
            "lat": float(ap["lat"]),
            "lon": float(ap["lon"]),
            "distance_km": float(ap["distance_km"]),
            "bus_arrival_name": ap["bus_arrival_name"],
        },
        "bus_options": bus_options,
        "flight_options": flight_options,
        "min_total_price": min_total,
    }


def find_cheap_ground_plus_flight(
    departure_city: str,
    arrival_id: str,
    outbound_date: str,
    ground_date: str | None = None,
    max_distance_km: int = 300,
    limit: int = 5,
    flight_cap_best: int = 3,
    flight_cap_other: int = 3,
    min_transfer_hours: float = 2.0,
) -> list[dict]:
    """Bus from departure_city → nearby airport, then flight → arrival_id.

    Returns a list of hub dicts (one per candidate airport) each with
    `bus_options`, `flight_options`, and a `min_total_price` honoring
    the 2-hour transfer rule. Pair selection is deferred to the caller.
    """
    if ground_date is None:
        ground_date = outbound_date

    nearby = find_nearby_airports(departure_city, max_distance_km, limit)
    if nearby.empty:
        print(f"⚠️  No airports within {max_distance_km}km of {departure_city!r}.")
        return []

    dep_query = departure_city.split(",")[0].strip()
    dep_query_norm = dep_query.lower().strip()

    # Skip hubs whose city is the same as the departure city — a bus from a
    # city to itself makes no sense and FlixBus would return nothing.
    nearby = nearby[nearby["city"].str.lower().str.strip() != dep_query_norm].reset_index(drop=True)
    if nearby.empty:
        print(f"⚠️  All nearby airports share the departure city ({dep_query!r}). No bus leg possible.")
        return []

    # Drop hubs that are currently in reliability suspension — they've burned
    # SerpAPI credits with consecutive empty results and aren't worth retrying
    # right now (see airport_reliability.py).
    suspended = [c for c in nearby["iata"].tolist() if is_suspended(c)]
    if suspended:
        print(f"   ⏸  Skipping suspended airports: {suspended}")
        nearby = nearby[~nearby["iata"].isin(suspended)].reset_index(drop=True)
    if nearby.empty:
        print("⚠️  All nearby airports are currently suspended. Try again later.")
        return []

    print(
        f"Found {len(nearby)} nearby airport(s) for {departure_city!r}; "
        f"fetching flights to {arrival_id} + buses for each (parallel)."
    )

    hub_rows = list(nearby.iterrows())
    with ThreadPoolExecutor(max_workers=min(5, len(hub_rows) or 1)) as pool:
        futures = [
            pool.submit(
                _fetch_hub,
                row,
                arrival_id,
                outbound_date,
                ground_date,
                dep_query,
                flight_cap_best,
                flight_cap_other,
                min_transfer_hours,
            )
            for _, row in hub_rows
        ]
        hubs = [f.result() for f in futures]

    hubs = [h for h in hubs if h is not None]
    if not hubs:
        print("⚠️  No hub returned both buses and flights.")
        return []

    # Sort: hubs with a valid 2h-rule combo first, cheapest min_total first.
    def sort_key(h):
        m = h.get("min_total_price")
        return (0, m) if m is not None else (1, float("inf"))

    hubs.sort(key=sort_key)
    return hubs


if __name__ == "__main__":
    hubs = find_cheap_ground_plus_flight(
        departure_city="Venice",
        arrival_id="NUE",
        outbound_date="2026-05-10",
    )
    if not hubs:
        print("No hubs found.")
    else:
        for h in hubs:
            print(
                f"{h['hub']['iata']:>4}  {h['hub']['city']:<14}"
                f"  buses={len(h['bus_options']):>2}"
                f"  flights={len(h['flight_options']):>2}"
                f"  min_total={h['min_total_price']}"
            )
