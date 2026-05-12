"""Bus → flight → bus pipeline, pair-card-grouped.

Pipeline shape:

  1. Find 3 closest IT/DE airports near departure_city (origin hubs).
  2. For each origin hub, call SerpAPI Google Travel Explore to discover
     destination airports within 300km of arrival_city. Cross-border hubs
     (ZRH, PRG, AMS, …) are kept on the *destination* side only — origin
     stays IT/DE-only.
  3. For each (origin_hub, dest_hub) pair, fetch non-stop flights and the
     two FlixBus legs (origin_city → origin_hub.city, dest_hub.city →
     arrival_city), all in parallel.
  4. Compute the default trio for each pair: cheapest valid three-leg total
     that respects the ≥ 2h transfer rule, using trip duration and transfer
     tightness as tie-breakers.

Returns one pair-card per (origin_hub, dest_hub) pair, sorted ascending by
the trio's min_total_price. Pairs without a valid trio are dropped.

Caching is automatic — flight_search() and get_trips() both go through
data_cache, so repeat searches reuse the underlying flight/bus rows.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from serpapi import Client as SerpApiClient

from .airport_reliability import is_suspended
from .bus_and_flight_search import (
    _bus_option,
    _flight_option,
    _to_naive,
    find_nearby_airports,
)
from .flight_and_ground_search import COUNTRY_EN, geocode_city, haversine_km
from .flight_search import flight_search
from .flixbus_finder import get_trips


_GLOBAL_AIRPORTS_FILE = Path(__file__).parent.parent / "data" / "airports.json"
_GLOBAL_LOOKUP: dict[str, dict] | None = None

# If a same-day bus already gives the cheapest flight at least this many
# hours of buffer (well above the 2h hard transfer rule), skip the prev/
# next-day FlixBus query — the user won't pick the overnight option, and
# the extra round trip just burns time on cache misses.
_COMFORT_HOURS = 3.0


def _global_iata_lookup() -> dict[str, dict]:
    """Lazy IATA → meta map built from the global airports.json. Used to
    resolve cross-border explore hits (ZRH, PRG, AMS) that aren't in the
    IT/DE-only filtered list. Returns {} if the file isn't checked out;
    the caller treats an empty lookup as 'no cross-border surfacing'.
    """
    global _GLOBAL_LOOKUP
    if _GLOBAL_LOOKUP is not None:
        return _GLOBAL_LOOKUP
    if not _GLOBAL_AIRPORTS_FILE.exists():
        _GLOBAL_LOOKUP = {}
        return _GLOBAL_LOOKUP
    try:
        with open(_GLOBAL_AIRPORTS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        _GLOBAL_LOOKUP = {}
        return _GLOBAL_LOOKUP
    out: dict[str, dict] = {}
    for row in data.values():
        iata = row.get("iata")
        if not iata or "lat" not in row or "lon" not in row:
            continue
        city = (row.get("city") or "").strip()
        if not city:
            continue
        out[iata.upper()] = {
            "iata": iata.upper(),
            "city": city,
            "country": (row.get("country") or "").strip(),
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
        }
    _GLOBAL_LOOKUP = out
    return out


def _explore_dest_hubs_anywhere(
    departure_id: str,
    target_city: str,
    outbound_date: str,
    max_distance_km: int = 300,
    limit: int = 3,
) -> pd.DataFrame:
    """Variant of flight_plus_bus_search._explore_select_hubs that allows
    cross-border destination airports (ZRH, PRG, AMS) by resolving IATAs
    against the global airports.json instead of the IT/DE airports_df.

    Returns the `limit` *closest* destination airports within
    max_distance_km of target_city, sorted by distance (not price — price
    sort happens later in the trio selection). Empty DataFrame on any
    failure (caller falls through to no-pair-found).
    """
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return pd.DataFrame()

    try:
        target_lat, target_lon = geocode_city(target_city)
    except Exception as e:
        print(f"   explore: geocode failed for {target_city!r}: {e}")
        return pd.DataFrame()

    iata_to_meta = _global_iata_lookup()
    if not iata_to_meta:
        # Without the global file we can't safely surface cross-border
        # hubs (no city resolution = no FlixBus search possible). Bail.
        print("   explore: data/airports.json not present — cannot surface non-IT/DE hubs.")
        return pd.DataFrame()

    print(f"🧭 Explore search STARTING: {departure_id} → near {target_city} on {outbound_date}")
    try:
        client = SerpApiClient(api_key=api_key)
        results = client.search({
            "engine": "google_travel_explore",
            "departure_id": departure_id.upper(),
            "currency": "EUR",
            "type": "2",
            "outbound_date": outbound_date,
            "no_cache": False,
        })
    except Exception as e:
        print(f"   explore({departure_id}) raised: {e}")
        return pd.DataFrame()

    target_norm = target_city.split(",")[0].strip().lower()
    rows = []
    for dest in results.get("destinations", []) or []:
        if "flight_price" not in dest:
            continue
        airport = dest.get("destination_airport") or {}
        iata = (airport.get("code") or "").upper()
        if not iata:
            continue
        meta = iata_to_meta.get(iata)
        if meta is None:
            continue
        city = meta["city"]
        if city.split(",")[0].strip().lower() == target_norm:
            continue
        # Skip same-airport "round-trip" — explore can echo the departure.
        if iata == departure_id.upper():
            continue
        d_km = haversine_km(meta["lat"], meta["lon"], target_lat, target_lon)
        if d_km > max_distance_km:
            continue
        country = meta["country"]
        country_en = COUNTRY_EN.get(country, country) or country
        rows.append({
            "iata": iata,
            "city": city,
            "country": country,
            "lat": meta["lat"],
            "lon": meta["lon"],
            "distance_km": round(d_km, 1),
            "bus_arrival_name": f"{city}, {country_en}" if country_en else city,
            "explore_price": float(dest["flight_price"]),
        })

    if not rows:
        print("   explore: 0 destinations within range.")
        return pd.DataFrame()

    df = (
        pd.DataFrame(rows)
        .sort_values("distance_km")
        .drop_duplicates(subset="iata", keep="first")
        .head(limit)
        .reset_index(drop=True)
    )
    print(
        f"   explore: {len(df)} dest hub(s) selected by distance "
        f"({', '.join(f'{r.iata}={r.distance_km}km' for r in df.itertuples())})"
    )
    return df


def _has_comfortable_bus_before_flight(
    flight: dict,
    bus_options: list[dict],
    outbound_date: str,
    comfort_hours: float = _COMFORT_HOURS,
) -> bool:
    """True if any priced bus arrives ≥ comfort_hours before flight takeoff.
    Used to decide whether the prev-day FlixBus query is worth running.
    """
    f_dep = _to_naive(flight.get("departure_time"), default_date=outbound_date)
    if f_dep is None:
        return False
    deadline = f_dep - timedelta(hours=comfort_hours)
    for b in bus_options:
        if b.get("price_eur") is None:
            continue
        b_arr = _to_naive(b.get("arrival_dt"), default_date=outbound_date)
        if b_arr is not None and b_arr <= deadline:
            return True
    return False


def _has_comfortable_bus_after_flight(
    flight: dict,
    bus_options: list[dict],
    outbound_date: str,
    comfort_hours: float = _COMFORT_HOURS,
) -> bool:
    """True if any priced bus departs ≥ comfort_hours after flight landing.
    Used to decide whether the next-day FlixBus query is worth running.
    """
    f_arr = _to_naive(flight.get("arrival_time"), default_date=outbound_date)
    if f_arr is None:
        return False
    earliest = f_arr + timedelta(hours=comfort_hours)
    for b in bus_options:
        if b.get("price_eur") is None:
            continue
        b_dep = _to_naive(b.get("departure_dt"), default_date=outbound_date)
        if b_dep is not None and b_dep >= earliest:
            return True
    return False


def _default_trio(
    bus1_options: list[dict],
    flight_options: list[dict],
    bus2_options: list[dict],
    outbound_date: str,
    min_transfer_hours: float,
    bus1_prev_options: list[dict] | None = None,
    bus2_next_options: list[dict] | None = None,
) -> dict | None:
    """Return the default valid trio across all three leg arrays:
        {"bus1_idx": int, "bus1_source": "same"|"prev",
         "flight_idx": int,
         "bus2_idx": int, "bus2_source": "same"|"next"}

    Selection rule (per product spec): walk flights from cheapest to most
    expensive; for the first flight that has BOTH a valid bus1 (arrives
    ≥ min_transfer_hours before takeoff) and a valid bus2 (departs
    ≥ min_transfer_hours after landing), pick the tightest connections —
    latest-arriving valid bus1, earliest-departing valid bus2 across the
    combined same-day + overnight pool. Same-day is preferred on ties to
    avoid surfacing an overnight stay when a same-day option is equally
    tight. Returns None if no flight has both sides covered.
    """
    threshold = timedelta(hours=min_transfer_hours)
    bus1_prev_options = bus1_prev_options or []
    bus2_next_options = bus2_next_options or []

    bus1_all = (
        [("same", i, b) for i, b in enumerate(bus1_options)]
        + [("prev", i, b) for i, b in enumerate(bus1_prev_options)]
    )
    bus2_all = (
        [("same", i, b) for i, b in enumerate(bus2_options)]
        + [("next", i, b) for i, b in enumerate(bus2_next_options)]
    )

    priced_flights = [
        (fi, f) for fi, f in enumerate(flight_options)
        if f.get("price_eur") is not None
    ]
    priced_flights.sort(key=lambda pair: (pair[1]["price_eur"], pair[0]))

    for fi, f in priced_flights:
        f_dep = _to_naive(f.get("departure_time"), default_date=outbound_date)
        f_arr = _to_naive(f.get("arrival_time"), default_date=outbound_date)
        if f_dep is None or f_arr is None:
            continue

        # Tightest valid bus1 across same+prev. On tied arrival time, prefer
        # same-day (source rank: "same" beats "prev") so we don't push the
        # user into an overnight stay when same-day works equally well.
        best_b1: tuple | None = None  # (arr_dt, source_rank, source, idx)
        for src, bi, b in bus1_all:
            if b.get("price_eur") is None:
                continue
            b_arr = _to_naive(b.get("arrival_dt"), default_date=outbound_date)
            if b_arr is None or b_arr > f_dep - threshold:
                continue
            src_rank = 0 if src == "same" else 1
            key = (b_arr, -src_rank)
            if best_b1 is None or key > (best_b1[0], -best_b1[1]):
                best_b1 = (b_arr, src_rank, src, bi)
        if best_b1 is None:
            continue

        best_b2: tuple | None = None  # (dep_dt, source_rank, source, idx)
        for src, bi, b in bus2_all:
            if b.get("price_eur") is None:
                continue
            b_dep = _to_naive(b.get("departure_dt"), default_date=outbound_date)
            if b_dep is None or b_dep < f_arr + threshold:
                continue
            src_rank = 0 if src == "same" else 1
            # Earlier dep wins; on tie, prefer same-day.
            key = (b_dep, src_rank)
            if best_b2 is None or key < (best_b2[0], best_b2[1]):
                best_b2 = (b_dep, src_rank, src, bi)
        if best_b2 is None:
            continue

        return {
            "bus1_idx": best_b1[3],
            "bus1_source": best_b1[2],
            "flight_idx": fi,
            "bus2_idx": best_b2[3],
            "bus2_source": best_b2[2],
        }

    return None


def _min_valid_total_three_legs(
    bus1_options: list[dict],
    flight_options: list[dict],
    bus2_options: list[dict],
    outbound_date: str,
    min_transfer_hours: float,
    bus1_prev_options: list[dict] | None = None,
    bus2_next_options: list[dict] | None = None,
) -> float | None:
    """Cheapest bus1.price + flight.price + bus2.price across trios that
    satisfy the 2h rule on both transfers, considering same-day + overnight
    buses on each side. None if no valid trio exists.

    Independent of _default_trio — used for sort/summary, not selection.
    """
    threshold = timedelta(hours=min_transfer_hours)
    bus1_all = list(bus1_options) + list(bus1_prev_options or [])
    bus2_all = list(bus2_options) + list(bus2_next_options or [])
    best: float | None = None
    for f in flight_options:
        if f.get("price_eur") is None:
            continue
        f_dep = _to_naive(f.get("departure_time"), default_date=outbound_date)
        f_arr = _to_naive(f.get("arrival_time"), default_date=outbound_date)
        if f_dep is None or f_arr is None:
            continue
        bus1_deadline = f_dep - threshold
        bus2_earliest = f_arr + threshold

        cheapest_bus1: float | None = None
        for b in bus1_all:
            if b.get("price_eur") is None:
                continue
            b_arr = _to_naive(b.get("arrival_dt"), default_date=outbound_date)
            if b_arr is None or b_arr > bus1_deadline:
                continue
            if cheapest_bus1 is None or b["price_eur"] < cheapest_bus1:
                cheapest_bus1 = b["price_eur"]
        if cheapest_bus1 is None:
            continue

        cheapest_bus2: float | None = None
        for b in bus2_all:
            if b.get("price_eur") is None:
                continue
            b_dep = _to_naive(b.get("departure_dt"), default_date=outbound_date)
            if b_dep is None or b_dep < bus2_earliest:
                continue
            if cheapest_bus2 is None or b["price_eur"] < cheapest_bus2:
                cheapest_bus2 = b["price_eur"]
        if cheapest_bus2 is None:
            continue

        total = cheapest_bus1 + f["price_eur"] + cheapest_bus2
        if best is None or total < best:
            best = total
    return best


def _fetch_pair(
    origin_hub: pd.Series,
    dest_hub: pd.Series,
    departure_city: str,
    arrival_city: str,
    outbound_date: str,
    flight_cap_best: int,
    min_transfer_hours: float,
) -> dict | None:
    """One (origin_hub, dest_hub) pair. Returns pair-card dict or None if
    any leg is empty or no valid trio exists.

    Flights are filtered to non-stop "Best" rows only per requirements.
    """
    try:
        flights_df = flight_search(
            origin_hub["iata"], dest_hub["iata"], outbound_date,
            track_iata=dest_hub["iata"],
        )
    except Exception as e:
        print(f"   flight_search({origin_hub['iata']} → {dest_hub['iata']}) raised: {e}")
        return None
    if flights_df is None or flights_df.empty or "flight_type" not in flights_df.columns:
        return None
    flights_cached_at = flights_df.attrs.get("cached_at")

    # Non-stop only, then top N by price. We don't filter on flight_type
    # ("Best" vs "Other") because Google Flights' bucketing is inconsistent
    # for thinner routes — a single non-stop on a regional pair often lands
    # in "Other". The price-sorted head is what enforces "lean depth."
    flights_df = flights_df[(flights_df["stops"] == 0) & flights_df["price"].notna()]
    if flights_df.empty:
        return None
    flights_df = (
        flights_df.sort_values("price")
        .head(flight_cap_best)
        .reset_index(drop=True)
    )

    try:
        bus1_df = get_trips(departure_city, origin_hub["city"], outbound_date)
    except Exception as e:
        print(f"   get_trips({departure_city} → {origin_hub['city']}) raised: {e}")
        return None
    if bus1_df is None or bus1_df.empty:
        return None
    bus1_cached_at = bus1_df.attrs.get("cached_at")

    try:
        bus2_df = get_trips(dest_hub["city"], arrival_city, outbound_date)
    except Exception as e:
        print(f"   get_trips({dest_hub['city']} → {arrival_city}) raised: {e}")
        return None
    if bus2_df is None or bus2_df.empty:
        return None
    bus2_cached_at = bus2_df.attrs.get("cached_at")

    def _filter_bus(df: pd.DataFrame, dt_col: str) -> pd.DataFrame:
        df = df.copy()
        df["_dt_naive"] = df[dt_col].map(_to_naive)
        df = df[df["_dt_naive"].notna() & df["price_eur"].notna()]
        return df.sort_values("_dt_naive").reset_index(drop=True)

    bus1_df = _filter_bus(bus1_df, "departure_dt")
    bus2_df = _filter_bus(bus2_df, "departure_dt")
    if bus1_df.empty or bus2_df.empty:
        return None

    flight_options = [
        _flight_option(i, r, origin_hub["iata"], dest_hub["iata"])
        for i, (_, r) in enumerate(flights_df.iterrows())
    ]
    bus1_options = [_bus_option(i, r) for i, (_, r) in enumerate(bus1_df.iterrows())]
    bus2_options = [_bus_option(i, r) for i, (_, r) in enumerate(bus2_df.iterrows())]

    # Overnight options: prev-day bus1 (D-1) lets the user catch a very-early
    # flight by riding the night before; next-day bus2 (D+1) lets the user
    # take a late flight and bus onward the morning after. We only query them
    # when the cheapest same-day flight DOESN'T already have a comfortable
    # (≥ _COMFORT_HOURS) bus on that side — otherwise the overnight would
    # never be picked and the extra FlixBus call is wasted on cache misses.
    cheapest_flight = flight_options[0] if flight_options else None
    skip_prev_query = cheapest_flight is not None and _has_comfortable_bus_before_flight(
        cheapest_flight, bus1_options, outbound_date,
    )
    skip_next_query = cheapest_flight is not None and _has_comfortable_bus_after_flight(
        cheapest_flight, bus2_options, outbound_date,
    )

    try:
        prev_date = (datetime.strptime(outbound_date, "%Y-%m-%d").date()
                     - timedelta(days=1)).isoformat()
        next_date = (datetime.strptime(outbound_date, "%Y-%m-%d").date()
                     + timedelta(days=1)).isoformat()
    except ValueError:
        prev_date = next_date = None

    bus1_prev_options: list[dict] = []
    if not skip_prev_query and prev_date:
        try:
            bus1_prev_df = get_trips(departure_city, origin_hub["city"], prev_date)
        except Exception as e:
            print(f"   get_trips({departure_city} → {origin_hub['city']}, {prev_date}) raised: {e}")
            bus1_prev_df = None
        if bus1_prev_df is not None and not bus1_prev_df.empty:
            prev_df = _filter_bus(bus1_prev_df, "departure_dt")
            bus1_prev_options = [_bus_option(i, r) for i, (_, r) in enumerate(prev_df.iterrows())]

    bus2_next_options: list[dict] = []
    if not skip_next_query and next_date:
        try:
            bus2_next_df = get_trips(dest_hub["city"], arrival_city, next_date)
        except Exception as e:
            print(f"   get_trips({dest_hub['city']} → {arrival_city}, {next_date}) raised: {e}")
            bus2_next_df = None
        if bus2_next_df is not None and not bus2_next_df.empty:
            next_df = _filter_bus(bus2_next_df, "departure_dt")
            bus2_next_options = [_bus_option(i, r) for i, (_, r) in enumerate(next_df.iterrows())]

    trio = _default_trio(
        bus1_options, flight_options, bus2_options,
        outbound_date, min_transfer_hours,
        bus1_prev_options=bus1_prev_options,
        bus2_next_options=bus2_next_options,
    )
    if trio is None:
        return None

    min_total = _min_valid_total_three_legs(
        bus1_options, flight_options, bus2_options,
        outbound_date, min_transfer_hours,
        bus1_prev_options=bus1_prev_options,
        bus2_next_options=bus2_next_options,
    )

    explore_price = dest_hub.get("explore_price") if "explore_price" in dest_hub else None
    return {
        "origin_hub": {
            "iata": origin_hub["iata"],
            "city": origin_hub["city"],
            "country": origin_hub["country"],
            "country_en": COUNTRY_EN.get(origin_hub["country"], origin_hub["country"]),
            "lat": float(origin_hub["lat"]),
            "lon": float(origin_hub["lon"]),
            "distance_km": float(origin_hub["distance_km"]),
            "bus_arrival_name": origin_hub["bus_arrival_name"],
        },
        "dest_hub": {
            "iata": dest_hub["iata"],
            "city": dest_hub["city"],
            "country": dest_hub["country"],
            "country_en": COUNTRY_EN.get(dest_hub["country"], dest_hub["country"]) or dest_hub["country"],
            "lat": float(dest_hub["lat"]),
            "lon": float(dest_hub["lon"]),
            "distance_km": float(dest_hub["distance_km"]),
            "bus_arrival_name": dest_hub["bus_arrival_name"],
        },
        "bus1_options": bus1_options,
        "flight_options": flight_options,
        "bus2_options": bus2_options,
        "bus1_prev_options": bus1_prev_options,
        "bus2_next_options": bus2_next_options,
        "default_trio": trio,
        "min_total_price": min_total,
        "explore_price": float(explore_price) if pd.notna(explore_price) else None,
        "flights_cached_at": flights_cached_at,
        "bus1_cached_at": bus1_cached_at,
        "bus2_cached_at": bus2_cached_at,
    }


def find_cheap_bus_plus_flight_plus_bus(
    departure_city: str,
    arrival_city: str,
    outbound_date: str,
    *,
    max_distance_km: int = 300,
    origin_hub_limit: int = 3,
    dest_hub_limit: int = 3,
    flight_cap_best: int = 3,
    min_transfer_hours: float = 2.0,
) -> list[dict]:
    """Bus → flight → bus, both ends IT/DE.

    Origin hubs come from the IT/DE airport list (3 closest). Dest hubs
    are discovered via Google Travel Explore (cross-border airports
    allowed). Returns list of pair-card dicts sorted ascending by
    min_total_price; pairs without a valid trio are dropped.
    """
    nearby = find_nearby_airports(departure_city, max_distance_km, origin_hub_limit)
    if nearby.empty:
        print(f"⚠️  No airports within {max_distance_km}km of {departure_city!r}.")
        return []

    dep_norm = departure_city.split(",")[0].strip().lower()
    nearby = nearby[nearby["city"].str.lower().str.strip() != dep_norm].reset_index(drop=True)
    if nearby.empty:
        print(f"⚠️  All nearby airports share the departure city ({departure_city!r}).")
        return []

    suspended = [c for c in nearby["iata"].tolist() if is_suspended(c)]
    if suspended:
        print(f"   ⏸  Skipping suspended origin hubs: {suspended}")
        nearby = nearby[~nearby["iata"].isin(suspended)].reset_index(drop=True)
    if nearby.empty:
        print("⚠️  All origin hubs currently suspended. Try again later.")
        return []

    print(
        f"Found {len(nearby)} origin hub(s) for {departure_city!r}; "
        f"running Explore for each to discover dest hubs near {arrival_city!r}."
    )

    arr_norm = arrival_city.split(",")[0].strip().lower()
    pairs: list[tuple[pd.Series, pd.Series]] = []
    for _, origin in nearby.iterrows():
        dest_hubs = _explore_dest_hubs_anywhere(
            origin["iata"], arrival_city, outbound_date,
            max_distance_km=max_distance_km, limit=dest_hub_limit,
        )
        if dest_hubs.empty:
            continue
        for _, dest in dest_hubs.iterrows():
            if str(dest["city"]).split(",")[0].strip().lower() == arr_norm:
                continue
            pairs.append((origin, dest))

    if not pairs:
        print("⚠️  No (origin, dest) pairs found via Explore.")
        return []

    print(f"   → {len(pairs)} pair(s) to fetch flights+buses for (parallel).")

    with ThreadPoolExecutor(max_workers=min(5, len(pairs))) as pool:
        futures = [
            pool.submit(
                _fetch_pair, origin, dest, departure_city, arrival_city,
                outbound_date, flight_cap_best, min_transfer_hours,
            )
            for origin, dest in pairs
        ]
        results = [f.result() for f in futures]

    cards = [r for r in results if r is not None]
    if not cards:
        print("⚠️  No pair returned a valid trio.")
        return []

    cards.sort(key=lambda c: c.get("min_total_price") if c.get("min_total_price") is not None else float("inf"))
    return cards


if __name__ == "__main__":
    cards = find_cheap_bus_plus_flight_plus_bus(
        departure_city="Venice",
        arrival_city="Berlin",
        outbound_date="2026-05-10",
    )
    if not cards:
        print("No pair-cards found.")
    else:
        for c in cards:
            o = c["origin_hub"]["iata"]
            d = c["dest_hub"]["iata"]
            print(
                f"{o}→{d}  bus1={len(c['bus1_options'])}  "
                f"flights={len(c['flight_options'])}  bus2={len(c['bus2_options'])}  "
                f"min_total={c['min_total_price']}"
            )
