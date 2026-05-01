"""Flight → ground (bus) pipeline, hub-grouped.

Mirror of bus_and_flight_search.py reversed: take the user's departure
airport and target arrival city, find airports near the target city
within max_distance_km, and for each pull SerpAPI's top 3 "best" + top 3
"other" flights from departure_id to that hub airport plus all FlixBus
options from that hub city to the target city.

Returns one dict per hub with `bus_options[]` and `flight_options[]`
attached raw so the frontend can let users pair flights with buses.

The 2-hour minimum transfer rule (flight arrival → bus departure)
is reflected only in `min_total_price`; the cross-product is deferred
to the caller.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd

from airport_reliability import is_suspended
from bus_and_flight_search import (
    _bus_option,
    _flight_option,
    _to_naive,
    find_nearby_airports,
)
from flight_and_ground_search import COUNTRY_EN
from flight_search import flight_search
from flixbus_finder import get_trips


def _min_valid_total_flight_first(
    bus_options: list[dict],
    flight_options: list[dict],
    outbound_date: str,
    min_transfer_hours: float,
) -> float | None:
    """Cheapest flight_price + bus_price across pairs where the bus
    departs at least `min_transfer_hours` after the flight lands.
    None if no pair valid.
    """
    threshold = timedelta(hours=min_transfer_hours)
    best: float | None = None
    for f in flight_options:
        if f["price_eur"] is None:
            continue
        f_arr = _to_naive(f.get("arrival_time"), default_date=outbound_date)
        if f_arr is None:
            continue
        earliest_bus = f_arr + threshold
        for b in bus_options:
            if b["price_eur"] is None:
                continue
            b_dep = _to_naive(b.get("departure_dt"), default_date=outbound_date)
            if b_dep is None or b_dep < earliest_bus:
                continue
            total = b["price_eur"] + f["price_eur"]
            if best is None or total < best:
                best = total
    return best


def _fetch_hub(
    ap: pd.Series,
    departure_id: str,
    target_city: str,
    outbound_date: str,
    ground_date: str,
    flight_cap_best: int,
    flight_cap_other: int,
    min_transfer_hours: float,
) -> dict | None:
    """Pull flights (departure_id → hub) + buses (hub → target_city)."""
    try:
        flights_df = flight_search(departure_id, ap["iata"], outbound_date,
                                   track_iata=ap["iata"])
    except Exception as e:
        print(f"   flight_search({departure_id} → {ap['iata']}) raised: {e}")
        return None
    if flights_df is None or flights_df.empty or "flight_type" not in flights_df.columns:
        return None

    best_subset = flights_df[flights_df["flight_type"] == "Best"].head(flight_cap_best)
    other_subset = flights_df[flights_df["flight_type"] == "Other"].head(flight_cap_other)
    flights_df = pd.concat([best_subset, other_subset], ignore_index=True)
    flights_df = flights_df[flights_df["price"].notna()]
    if flights_df.empty:
        return None

    target_query = target_city.split(",")[0].strip()

    try:
        ground_df = get_trips(ap["city"], target_query, ground_date)
    except Exception as e:
        print(f"   get_trips({ap['city']} → {target_query}) raised: {e}")
        return None
    if ground_df is None or ground_df.empty:
        return None

    ground_df = ground_df.copy()
    ground_df["_dep_naive"] = ground_df["departure_dt"].map(_to_naive)
    ground_df = ground_df[
        ground_df["_dep_naive"].notna() & ground_df["price_eur"].notna()
    ].reset_index(drop=True)
    if ground_df.empty:
        return None

    flight_options = [
        _flight_option(i, r, departure_id, ap["iata"])
        for i, (_, r) in enumerate(flights_df.iterrows())
    ]
    bus_options = [
        _bus_option(i, r) for i, (_, r) in enumerate(ground_df.iterrows())
    ]
    min_total = _min_valid_total_flight_first(
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


def find_cheap_flight_plus_ground_v2(
    departure_id: str,
    target_city: str,
    outbound_date: str,
    ground_date: str | None = None,
    max_distance_km: int = 300,
    limit: int = 5,
    flight_cap_best: int = 3,
    flight_cap_other: int = 3,
    min_transfer_hours: float = 2.0,
) -> list[dict]:
    """Flight from departure_id → nearby hub airport, then bus → target_city.

    Returns a list of hub dicts symmetric with `find_cheap_ground_plus_flight`.
    """
    if ground_date is None:
        ground_date = outbound_date

    nearby = find_nearby_airports(target_city, max_distance_km, limit)
    if nearby.empty:
        print(f"⚠️  No airports within {max_distance_km}km of {target_city!r}.")
        return []

    # Skip hubs whose city is the same as the target city — bus from hub to
    # destination wouldn't exist if they're the same place.
    target_query = target_city.split(",")[0].strip().lower()
    nearby = nearby[nearby["city"].str.lower().str.strip() != target_query].reset_index(drop=True)
    if nearby.empty:
        print(f"⚠️  All nearby airports are in the destination city ({target_city!r}). No bus leg possible.")
        return []

    # Drop hubs currently suspended for unreliable SerpAPI results.
    suspended = [c for c in nearby["iata"].tolist() if is_suspended(c)]
    if suspended:
        print(f"   ⏸  Skipping suspended airports: {suspended}")
        nearby = nearby[~nearby["iata"].isin(suspended)].reset_index(drop=True)
    if nearby.empty:
        print("⚠️  All nearby airports are currently suspended. Try again later.")
        return []

    print(
        f"Found {len(nearby)} nearby airport(s) for {target_city!r}; "
        f"fetching flights from {departure_id} + buses for each (parallel)."
    )

    hub_rows = list(nearby.iterrows())
    with ThreadPoolExecutor(max_workers=min(5, len(hub_rows) or 1)) as pool:
        futures = [
            pool.submit(
                _fetch_hub,
                row,
                departure_id,
                target_city,
                outbound_date,
                ground_date,
                flight_cap_best,
                flight_cap_other,
                min_transfer_hours,
            )
            for _, row in hub_rows
        ]
        hubs = [f.result() for f in futures]

    hubs = [h for h in hubs if h is not None]
    if not hubs:
        print("⚠️  No hub returned both flights and buses.")
        return []

    def sort_key(h):
        m = h.get("min_total_price")
        return (0, m) if m is not None else (1, float("inf"))

    hubs.sort(key=sort_key)
    return hubs


if __name__ == "__main__":
    hubs = find_cheap_flight_plus_ground_v2(
        departure_id="VCE",
        target_city="Nuremberg",
        outbound_date="2026-05-10",
    )
    if not hubs:
        print("No hubs found.")
    else:
        for h in hubs:
            print(
                f"{h['hub']['iata']:>4}  {h['hub']['city']:<14}"
                f"  flights={len(h['flight_options']):>2}"
                f"  buses={len(h['bus_options']):>2}"
                f"  min_total={h['min_total_price']}"
            )
