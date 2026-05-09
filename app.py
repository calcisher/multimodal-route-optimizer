"""Flask backend that wires the existing React UI to flight_search and the hub-grouped flight+bus / bus+flight pipelines."""
from __future__ import annotations

import json
import math
import os
import unicodedata
from datetime import date as date_cls, datetime
from pathlib import Path

import httpx
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request, send_from_directory

from backend import data_cache
from backend.bus_and_flight_search import _to_naive, find_cheap_ground_plus_flight
from backend.bus_flight_bus_search import find_cheap_bus_plus_flight_plus_bus
from backend.flight_and_ground_search import airports_df, geocode_city, haversine_km
from backend.flight_plus_bus_search import find_cheap_flight_plus_ground_v2
from backend.flight_search import flight_search
from backend.flixbus_finder import get_trips as bus_direct_get_trips
from backend.train_finder import get_trips as train_get_trips

# IT/DE airport list — drives the autocomplete and the nearby-airports search.
with open(Path(__file__).parent / "data" / "filtered_airports_it_de.json") as _f:
    _ALL = json.load(_f)

# Global IATA → coords lookup. The flight responses we return reference any
# IATA SerpAPI surfaces as a layover or via-hub (PMI, IST, STR, …) — those
# aren't in the IT/DE list, but the UI map still needs lat/lon to plot them.
# Falls back gracefully if the global file isn't checked out (it's a 9MB blob
# kept out of git on some setups).
_GLOBAL_FILE = Path(__file__).parent / "data" / "airports.json"
_GLOBAL_ALL: dict = {}
if _GLOBAL_FILE.exists():
    try:
        with open(_GLOBAL_FILE) as _gf:
            _GLOBAL_ALL = json.load(_gf)
    except (OSError, json.JSONDecodeError):
        _GLOBAL_ALL = {}

# A few city labels in the global JSON point at the legal/suburb city instead
# of the metro the airport actually serves (e.g. IST → "Arnavutkoy"). Override
# so the UI shows recognizable names. Only add entries the data gets wrong.
_CITY_OVERRIDES = {
    "IST": "Istanbul",
    "SAW": "Istanbul",
    "PMI": "Palma de Mallorca",
    "CDG": "Paris",
    "ORY": "Paris",
    "LHR": "London",
    "LGW": "London",
    "STN": "London",
    "JFK": "New York",
    "LGA": "New York",
    "EWR": "New York",
}

# Italian/German native names that we display in English. The curated IT/DE
# JSON is mostly English already; this catches the few stragglers (LIRA says
# "Roma" while LIRF says "Rome" — without normalization the autocomplete
# splits Rome into two city groups and only one airport gets searched).
_DISPLAY_CITY = {
    "Roma": "Rome",
    "Firenze": "Florence",
    "Genova": "Genoa",
    "Napoli": "Naples",
    "Torino": "Turin",
    "Venezia": "Venice",
    "Frankfurt-am-Main": "Frankfurt",
}


def _normalize_city(city: str | None) -> str | None:
    if not city:
        return city
    return _DISPLAY_CITY.get(city, city)

AIRPORT_COORDS: dict[str, dict] = {}
# Filtered file wins on conflict (curated) — global fills the gaps.
for row in _GLOBAL_ALL.values():
    iata = row.get("iata")
    if iata and "lat" in row and "lon" in row:
        AIRPORT_COORDS[iata] = {
            "lat": row["lat"],
            "lon": row["lon"],
            "city": _CITY_OVERRIDES.get(iata, _normalize_city(row.get("city", ""))),
            "country": row.get("country", ""),
        }
for row in _ALL.values():
    iata = row.get("iata")
    if iata:
        AIRPORT_COORDS[iata] = {
            "lat": row["lat"],
            "lon": row["lon"],
            "city": _CITY_OVERRIDES.get(iata, _normalize_city(row.get("city", ""))),
            "country": row.get("country", ""),
        }

_CITY_ALIASES = {
    "nurnberg": "nuremberg", "munchen": "munich", "koln": "cologne",
    "milano": "milan", "venezia": "venice", "roma": "rome",
    "firenze": "florence", "torino": "turin", "napoli": "naples",
}

# For cities with multiple airports, force the preferred (larger) one.
# Checked against airports_df at resolve time so typos don't hard-crash.
_PREFERRED_IATA = {
    "roma":  "FCO",  # Fiumicino > Ciampino
    "rome":  "FCO",
    "milan": "MXP",  # Malpensa > Linate
    "milano": "MXP",
}

ROOT = Path(__file__).parent
UI_DIR = ROOT / "UI"

app = Flask(__name__, static_folder=None)

# Drop expired/past-date cache rows at startup, then keep pruning every
# 2h so long-running servers don't accumulate stale rows. Cache is
# per-data-type (flight 6h, bus 48h) at the SerpAPI/FlixBus call sites in
# flight_search.py and flixbus_finder.py.
data_cache.prune()
data_cache.start_periodic_prune(interval_minutes=120)


# ---------- helpers ----------

def _city_coords(city: str) -> dict | None:
    """Return {lat, lon} for a city name by matching airports_df."""
    if not city:
        return None
    cn = unicodedata.normalize("NFKD", city).encode("ascii", "ignore").decode().lower().strip()
    try:
        matches = airports_df[airports_df["city"].str.lower().str.strip() == cn]
        if not matches.empty:
            row = matches.iloc[0]
            return {"lat": float(row["lat"]), "lon": float(row["lon"])}
    except Exception:
        pass
    return None


def _clean(v):
    """Convert NaN / NaT to None so JSON is clean."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp) and pd.isna(v):
        return None
    return v


def _fmt_minutes(total_min) -> str:
    total_min = _clean(total_min)
    if total_min is None:
        return ""
    total_min = int(total_min)
    h, m = divmod(total_min, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _time_of(dt_str) -> str:
    """Pull HH:MM from either '2026-05-10 05:55' or pandas Timestamp."""
    dt_str = _clean(dt_str)
    if dt_str is None:
        return ""
    if isinstance(dt_str, pd.Timestamp):
        return dt_str.strftime("%H:%M")
    s = str(dt_str)
    if " " in s and len(s) >= 16:
        return s.split(" ", 1)[1][:5]
    if len(s) >= 5:
        return s[:5]
    return s


def _date_of(dt) -> date_cls | None:
    dt = _clean(dt)
    if dt is None:
        return None
    if isinstance(dt, pd.Timestamp):
        return dt.date()
    s = str(dt)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _norm(s: str) -> str:
    """Lowercase + strip diacritics for fuzzy city matching."""
    return (
        unicodedata.normalize("NFKD", s or "")
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )


def resolve_iata(q: str) -> str | None:
    """Resolve a user-entered string (IATA code or city name) to an IATA code.

    Resolution order:
      1. Exact 3-letter IATA code in the DB
      2. Exact city name match (with alias expansion and diacritic stripping)
      3. Partial city/airport name match
      4. Geocode the city and return the nearest airport in the DB (fallback)
    """
    if not q:
        return None
    q = q.strip()
    if len(q) == 3 and q.isalpha():
        up = q.upper()
        if (airports_df["iata"] == up).any():
            return up
    base = _norm(q)

    # Preferred airport override for multi-airport cities.
    preferred = _PREFERRED_IATA.get(base)
    if preferred and (airports_df["iata"] == preferred).any():
        return preferred

    candidates = [base]
    if base in _CITY_ALIASES:
        candidates.append(_CITY_ALIASES[base])
    for k, v in _CITY_ALIASES.items():
        if v == base:
            candidates.append(k)
    city_norm = airports_df["city"].map(_norm)
    name_norm = airports_df["name"].map(_norm)
    for target in candidates:
        exact = airports_df[city_norm == target]
        if not exact.empty:
            return str(exact.iloc[0]["iata"])
    for target in candidates:
        partial = airports_df[city_norm.str.contains(target, na=False) | name_norm.str.contains(target, na=False)]
        if not partial.empty:
            return str(partial.iloc[0]["iata"])

    # Fallback: geocode the city and find the nearest airport in the DB.
    try:
        lat, lon = geocode_city(q)
        dists = airports_df.apply(
            lambda r: haversine_km(lat, lon, r["lat"], r["lon"]), axis=1
        )
        idx = dists.idxmin()
        nearest = airports_df.loc[idx]
        dist_km = dists[idx]
        print(f"ℹ️  resolve_iata: '{q}' not in DB → nearest airport "
              f"{nearest['iata']} ({nearest['city']}, {dist_km:.0f} km away)")
        return str(nearest["iata"])
    except Exception as e:
        print(f"⚠️  resolve_iata geocode fallback failed for '{q}': {e}")
        return None


def resolve_iatas(q: str) -> list[str]:
    """Expand a city name to ALL airports of that city.

    A 3-letter IATA → single-element list. A city name → every airport
    whose city (after English normalization + alias expansion) matches.
    Falls back to resolve_iata's single-result behavior when the city
    isn't in the DB. Returns [] for unresolvable input.

    Used by /api/flights so "Frankfurt → Milan" passes "MXP,LIN" to
    SerpAPI and gets results from both Milan airports in one call.
    """
    if not q:
        return []
    q = q.strip()
    if len(q) == 3 and q.isalpha():
        up = q.upper()
        if (airports_df["iata"] == up).any():
            return [up]

    base = _norm(q)
    candidates = {base}
    if base in _CITY_ALIASES:
        candidates.add(_CITY_ALIASES[base])
    for k, v in _CITY_ALIASES.items():
        if v == base:
            candidates.add(k)
    # Also fold display-overrides so "Roma" and "Rome" both expand to Rome's airports.
    for native, en in _DISPLAY_CITY.items():
        if _norm(en) in candidates:
            candidates.add(_norm(native))
        if _norm(native) in candidates:
            candidates.add(_norm(en))

    city_norm = airports_df["city"].map(_norm)
    matches = airports_df[city_norm.isin(candidates)]
    if not matches.empty:
        return [str(c) for c in matches["iata"].tolist() if c]

    single = resolve_iata(q)
    return [single] if single else []


def _to_float(v):
    v = _clean(v)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- transformers ----------

def _flight_row_to_ui(row: pd.Series) -> dict:
    legs_raw = row.get("legs") if "legs" in row.index else None
    legs_raw = legs_raw if isinstance(legs_raw, list) else []
    layovers_raw = row.get("layovers") if "layovers" in row.index else None
    layovers_raw = layovers_raw if isinstance(layovers_raw, list) else []

    if len(legs_raw) > 1:
        ui_legs = [
            {
                "dep": _time_of(l.get("dep")),
                "arr": _time_of(l.get("arr")),
                "from": l.get("from", ""),
                "to": l.get("to", ""),
                "duration": _fmt_minutes(l.get("duration")),
                "flightNo": l.get("flight_number", ""),
            }
            for l in legs_raw
        ]
        lay = layovers_raw[0] if layovers_raw else {}
        layover = {
            "airport": lay.get("airport", "") or legs_raw[0].get("to", ""),
            "city": lay.get("name", "") or lay.get("airport", "") or legs_raw[0].get("to", ""),
            "duration": _fmt_minutes(lay.get("duration")),
        }
        return {
            "airline": _clean(row.get("airline")) or "",
            "flightNo": " + ".join(l.get("flight_number", "") for l in legs_raw if l.get("flight_number")),
            "price": _to_float(row.get("price")),
            "depIata": legs_raw[0].get("from", "") or (_clean(row.get("departure_iata")) or ""),
            "arrIata": legs_raw[-1].get("to", "") or (_clean(row.get("arrival_iata")) or ""),
            "stops": int(_clean(row.get("stops")) or (len(legs_raw) - 1)),
            "totalDuration": _fmt_minutes(row.get("duration")),
            "legs": ui_legs,
            "layover": layover,
        }

    # Single-leg / direct
    return {
        "airline": _clean(row.get("airline")) or "",
        "flightNo": _clean(row.get("flight_number")) or "",
        "price": _to_float(row.get("price")),
        "dep": _time_of(row.get("departure_time")),
        "arr": _time_of(row.get("arrival_time")),
        "depIata": _clean(row.get("departure_iata")) or "",
        "arrIata": _clean(row.get("arrival_iata")) or "",
        "duration": _fmt_minutes(row.get("duration")),
        "stops": 0,
    }


CHEAP_FLIGHTS_LIMIT = 14


def transform_flights(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Split one flight_search DataFrame into (bestFlights, cheapFlights).

    Best is uncapped — SerpAPI's "Best" tab is already a curated short
    list (typically 2-5 results). Cheap is sorted by price and capped
    at CHEAP_FLIGHTS_LIMIT so multi-airport searches surface a few
    flights from each airport instead of getting the cheapest 8 all
    landing at the same IATA.
    """
    if df is None or df.empty:
        return [], []

    best_df = df[df["flight_type"] == "Best"].copy()
    other_df = df[df["flight_type"] != "Best"].copy()

    best = [_flight_row_to_ui(r) for _, r in best_df.sort_values("price").iterrows()]
    cheap = [
        _flight_row_to_ui(r)
        for _, r in other_df.sort_values("price").head(CHEAP_FLIGHTS_LIMIT).iterrows()
    ]
    return best, cheap


def _iso_or_none(dt, default_date: str | None) -> str | None:
    """Normalize a SerpAPI/FlixBus datetime to ISO 8601 (no tz) so the
    frontend can compute connection times. Falls back to clock+default_date.
    """
    nv = _to_naive(dt, default_date=default_date)
    if nv is None:
        return None
    return nv.isoformat()


def _bus_to_ui(b: dict, outbound_date: str, mode: str, departure_city: str,
               arrival_city: str, hub_city: str) -> dict:
    """Map a backend bus_option dict into the UI's bus payload."""
    dep_iso = _iso_or_none(b.get("departure_dt"), outbound_date)
    arr_iso = _iso_or_none(b.get("arrival_dt"), outbound_date)
    dep_d = _date_of(b.get("departure_dt"))
    arr_d = _date_of(b.get("arrival_dt"))
    try:
        outbound_d = datetime.strptime(outbound_date, "%Y-%m-%d").date()
    except ValueError:
        outbound_d = None
    next_day = bool(outbound_d and arr_d and arr_d > outbound_d)

    if mode == "bus_plus_flight":
        from_label = departure_city
        to_label = b.get("destination") or hub_city
    else:  # flight_plus_bus: bus runs hub -> arrival
        from_label = b.get("origin") or hub_city
        to_label = arrival_city

    return {
        "id": b.get("id"),
        "type": "Bus",
        "company": "FlixBus",
        "price": _to_float(b.get("price_eur")),
        "dep": _time_of(b.get("departure_dt")),
        "arr": _time_of(b.get("arrival_dt")),
        "depISO": dep_iso,
        "arrISO": arr_iso,
        "depDate": dep_d.isoformat() if dep_d else None,
        "arrDate": arr_d.isoformat() if arr_d else None,
        "nextDay": next_day,
        "from": from_label,
        "to": to_label,
        "duration": _fmt_minutes(b.get("duration_min")),
        "durationMin": int(b["duration_min"]) if b.get("duration_min") is not None else None,
        "url": b.get("url"),
    }


def _flight_to_ui(f: dict, outbound_date: str, hub_iata: str,
                  hub_city: str, dep_iata: str, arr_iata: str,
                  arrival_city: str, mode: str) -> dict:
    """Map a backend flight_option dict into the UI's flight payload."""
    dep_iso = _iso_or_none(f.get("departure_time"), outbound_date)
    arr_iso = _iso_or_none(f.get("arrival_time"), outbound_date)
    dep_d = _date_of(f.get("departure_time"))
    arr_d = _date_of(f.get("arrival_time"))
    try:
        outbound_d = datetime.strptime(outbound_date, "%Y-%m-%d").date()
    except ValueError:
        outbound_d = None
    next_day = bool(outbound_d and arr_d and arr_d > outbound_d)

    legs_raw = f.get("legs") or []
    layovers_raw = f.get("layovers") or []

    if mode == "bus_plus_flight":
        # flight runs hub -> arrival
        from_iata = f.get("departure_iata") or hub_iata
        to_iata = f.get("arrival_iata") or arr_iata
        to_city = arrival_city
    else:  # flight_plus_bus: flight runs origin -> hub
        from_iata = f.get("departure_iata") or dep_iata
        to_iata = f.get("arrival_iata") or hub_iata
        to_city = hub_city

    payload: dict = {
        "id": f.get("id"),
        "flightType": f.get("type", ""),
        "airline": f.get("airline", "") or "",
        "flightNo": f.get("flight_number", "") or "",
        "price": _to_float(f.get("price_eur")),
        "dep": _time_of(f.get("departure_time")),
        "arr": _time_of(f.get("arrival_time")),
        "depISO": dep_iso,
        "arrISO": arr_iso,
        "depDate": dep_d.isoformat() if dep_d else None,
        "arrDate": arr_d.isoformat() if arr_d else None,
        "nextDay": next_day,
        "fromIata": from_iata,
        "toIata": to_iata,
        "toCity": to_city,
        "duration": _fmt_minutes(f.get("duration_min")),
        "durationMin": int(f["duration_min"]) if f.get("duration_min") is not None else None,
        "stops": int(f.get("stops") or 0),
        "link": f.get("link"),
    }

    if len(legs_raw) > 1:
        payload["legs"] = [
            {
                "dep": _time_of(l.get("dep")),
                "arr": _time_of(l.get("arr")),
                "from": l.get("from", ""),
                "to": l.get("to", ""),
                "fromName": l.get("from_name", ""),
                "toName": l.get("to_name", ""),
                "duration": _fmt_minutes(l.get("duration")),
                "flightNo": l.get("flight_number", ""),
                "airline": l.get("airline", "") or f.get("airline", "") or "",
            }
            for l in legs_raw
        ]
        payload["layovers"] = [
            {
                "airport": lay.get("airport", ""),
                "city": lay.get("name", "") or lay.get("airport", ""),
                "duration": _fmt_minutes(lay.get("duration")),
            }
            for lay in layovers_raw
        ]
    return payload


def _ground_direct_to_ui(row: pd.Series, outbound_date: str,
                          departure_city: str, arrival_city: str,
                          transport_type: str, company: str) -> dict:
    dep_iso = _iso_or_none(row.get("departure_dt"), outbound_date)
    arr_iso = _iso_or_none(row.get("arrival_dt"), outbound_date)
    dep_d   = _date_of(row.get("departure_dt"))
    arr_d   = _date_of(row.get("arrival_dt"))
    try:
        outbound_d = datetime.strptime(outbound_date, "%Y-%m-%d").date()
    except ValueError:
        outbound_d = None
    next_day = bool(outbound_d and arr_d and arr_d > outbound_d)
    return {
        "type":        transport_type,
        "company":     company,
        "price":       _to_float(row.get("price_eur")),
        "dep":         _time_of(row.get("departure_dt")),
        "arr":         _time_of(row.get("arrival_dt")),
        "depISO":      dep_iso,
        "arrISO":      arr_iso,
        "depDate":     dep_d.isoformat() if dep_d else None,
        "arrDate":     arr_d.isoformat() if arr_d else None,
        "nextDay":     next_day,
        "from":        row.get("origin") or departure_city,
        "to":          row.get("destination") or arrival_city,
        "duration":    _fmt_minutes(row.get("duration_min")),
        "durationMin": int(row["duration_min"]) if row.get("duration_min") is not None else None,
        "stops":     int(row.get("stops") or 0),
        "url":       row.get("url"),
        "waypoints": row.get("waypoints") if isinstance(row.get("waypoints"), list) else [],
    }


def _train_to_ui(row: pd.Series, outbound_date: str,
                 departure_city: str, arrival_city: str) -> dict:
    return _ground_direct_to_ui(
        row, outbound_date, departure_city, arrival_city,
        "Train", row.get("provider") or "Train",
    )


def _bus_direct_to_ui(row: pd.Series, outbound_date: str,
                      departure_city: str, arrival_city: str) -> dict:
    return _ground_direct_to_ui(
        row, outbound_date, departure_city, arrival_city,
        "Bus", "FlixBus",
    )


def _enrich_hubs_with_trains(hubs: list[dict], from_city: str, to_city: str,
                              date_str: str, mode: str) -> None:
    """Search trains in parallel for each hub's ground leg and merge into busOptions."""
    def _fetch(hub_dict, idx):
        hub_city = hub_dict.get("hub", {}).get("city", "")
        if not hub_city:
            return idx, []
        origin = from_city if mode == "bus_plus_flight" else hub_city
        dest   = hub_city  if mode == "bus_plus_flight" else to_city
        try:
            df = train_get_trips(origin, dest, date_str)
            if df is not None and not df.empty:
                opts = [_train_to_ui(r, date_str, origin, dest) for _, r in df.iterrows()]
                for j, o in enumerate(opts):
                    o["id"] = f"trn-{idx}-{j}"
                return idx, opts
        except Exception:
            pass
        return idx, []

    if not hubs:
        return
    with ThreadPoolExecutor(max_workers=max(1, len(hubs))) as ex:
        for fut in [ex.submit(_fetch, h, i) for i, h in enumerate(hubs)]:
            idx, opts = fut.result()
            if opts:
                hubs[idx]["busOptions"].extend(opts)


def transform_hubs(
    hubs: list[dict],
    outbound_date: str,
    dep_iata: str,
    arr_iata: str,
    departure_city: str,
    arrival_city: str,
    mode: str,
) -> list[dict]:
    """Shape the hub-grouped backend output for the HubMasterCard frontend.

    `mode`: 'bus_plus_flight' (section 2) or 'flight_plus_bus' (section 3).
    """
    out = []
    for h in hubs:
        hub = h.get("hub") or {}
        hub_iata = hub.get("iata") or ""
        hub_city = hub.get("city") or ""
        bus_options = [
            _bus_to_ui(b, outbound_date, mode, departure_city, arrival_city, hub_city)
            for b in h.get("bus_options") or []
        ]
        flight_options = [
            _flight_to_ui(f, outbound_date, hub_iata, hub_city,
                          dep_iata, arr_iata, arrival_city, mode)
            for f in h.get("flight_options") or []
        ]
        out.append({
            "mode": mode,
            "hub": {
                "iata": hub_iata,
                "city": hub_city,
                "country": hub.get("country") or "",
                "countryEn": hub.get("country_en") or "",
                "distanceKm": _to_float(hub.get("distance_km")),
                "lat": _to_float(hub.get("lat")),
                "lon": _to_float(hub.get("lon")),
                "busArrivalName": hub.get("bus_arrival_name") or "",
            },
            "busOptions": bus_options,
            "flightOptions": flight_options,
            "minTotal": _to_float(h.get("min_total_price")),
            "depIata": dep_iata,
            "arrIata": arr_iata,
            "flightsCachedAt": h.get("flights_cached_at"),
            "busCachedAt": h.get("bus_cached_at"),
        })
    return out


def transform_bus_flight_bus_pairs(
    pairs: list[dict],
    outbound_date: str,
    departure_city: str,
    arrival_city: str,
) -> list[dict]:
    """Shape bus_flight_bus pair-cards for the BusFlightBusCard frontend.

    Each pair has two hubs (origin + dest) and three leg arrays. Bus1
    runs departure_city → origin_hub.city, the flight runs origin_hub →
    dest_hub, bus2 runs dest_hub.city → arrival_city. The default-trio
    indices come straight from the pipeline (cheapest flight + tightest
    valid buses).
    """
    out = []
    for p in pairs:
        origin = p.get("origin_hub") or {}
        dest = p.get("dest_hub") or {}
        origin_iata = origin.get("iata") or ""
        dest_iata = dest.get("iata") or ""
        origin_city = origin.get("city") or ""
        dest_city = dest.get("city") or ""

        bus1_options = [
            _bus_to_ui(b, outbound_date, "bus_plus_flight",
                       departure_city, dest_city, origin_city)
            for b in p.get("bus1_options") or []
        ]
        bus2_options = [
            _bus_to_ui(b, outbound_date, "flight_plus_bus",
                       departure_city, arrival_city, dest_city)
            for b in p.get("bus2_options") or []
        ]
        flight_options = [
            _flight_to_ui(f, outbound_date, origin_iata, origin_city,
                          origin_iata, dest_iata, dest_city, "bus_plus_flight")
            for f in p.get("flight_options") or []
        ]

        trio = p.get("default_trio") or {}
        out.append({
            "mode": "bus_flight_bus",
            "originHub": {
                "iata": origin_iata,
                "city": origin_city,
                "country": origin.get("country") or "",
                "countryEn": origin.get("country_en") or "",
                "distanceKm": _to_float(origin.get("distance_km")),
                "lat": _to_float(origin.get("lat")),
                "lon": _to_float(origin.get("lon")),
                "busArrivalName": origin.get("bus_arrival_name") or "",
            },
            "destHub": {
                "iata": dest_iata,
                "city": dest_city,
                "country": dest.get("country") or "",
                "countryEn": dest.get("country_en") or "",
                "distanceKm": _to_float(dest.get("distance_km")),
                "lat": _to_float(dest.get("lat")),
                "lon": _to_float(dest.get("lon")),
                "busArrivalName": dest.get("bus_arrival_name") or "",
            },
            "bus1Options": bus1_options,
            "flightOptions": flight_options,
            "bus2Options": bus2_options,
            "defaultTrio": {
                "bus1Idx": trio.get("bus1_idx"),
                "flightIdx": trio.get("flight_idx"),
                "bus2Idx": trio.get("bus2_idx"),
            },
            "minTotal": _to_float(p.get("min_total_price")),
            "explorePrice": _to_float(p.get("explore_price")),
            "depIata": origin_iata,
            "arrIata": dest_iata,
            "flightsCachedAt": p.get("flights_cached_at"),
            "bus1CachedAt": p.get("bus1_cached_at"),
            "bus2CachedAt": p.get("bus2_cached_at"),
        })
    return out


# ---------- routes ----------

@app.get("/")
def index():
    return send_from_directory(UI_DIR, "Multi Route.html")


@app.get("/css/<path:filename>")
def static_css(filename):
    return send_from_directory(UI_DIR / "css", filename)


@app.get("/js/<path:filename>")
def static_js(filename):
    return send_from_directory(UI_DIR / "js", filename)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/airports")
def airports():
    """List of airports for the frontend autocomplete (IT/DE only)."""
    rows = [
        {
            "iata": r.get("iata"),
            "icao": r.get("icao"),
            "name": r.get("name"),
            "city": _normalize_city(r.get("city")),
            "country": r.get("country"),
        }
        for r in _ALL.values()
        if r.get("iata") and r.get("city")
    ]
    return {"airports": rows}


def _resolve_inputs(payload: dict):
    """Returns ((from_iata, to_iata, from_city, to_city, date_str,
                 from_iatas, to_iatas), None) on success,
    or (None, (response, status)) on failure.

    `from_iata`/`to_iata` are the primary single-airport resolutions used by
    the hub pipelines. `from_iatas`/`to_iatas` are full lists for direct
    flight search — when the user gives a city name without locking a
    specific airport, we want SerpAPI to search across all airports of
    that city (e.g. Milan → MXP,LIN).
    """
    from_iata_raw = (payload.get("from_iata") or "").strip()
    to_iata_raw = (payload.get("to_iata") or "").strip()
    from_city_raw = (payload.get("from_city") or "").strip()
    to_city_raw = (payload.get("to_city") or "").strip()
    date_str = (payload.get("date") or "").strip()

    from_raw = from_iata_raw or from_city_raw
    to_raw = to_iata_raw or to_city_raw

    if not date_str:
        return None, (jsonify({"error": "date is required (YYYY-MM-DD)."}), 400)
    if not from_raw or not to_raw:
        return None, (jsonify({"error": "Both From and To are required."}), 400)

    from_iata = resolve_iata(from_raw)
    to_iata = resolve_iata(to_raw)
    if not from_iata or not to_iata:
        return None, (jsonify({
            "error": f"Could not resolve airports: "
                     f"from={from_raw!r}→{from_iata}, to={to_raw!r}→{to_iata}. "
                     f"Try an IATA code (e.g. VCE, NUE).",
        }), 400)

    # Expand city → multi-airport list ONLY when the user didn't lock a
    # specific airport. Locked IATA wins — single-airport search.
    from_iatas = [from_iata] if from_iata_raw else (resolve_iatas(from_city_raw) or [from_iata])
    to_iatas = [to_iata] if to_iata_raw else (resolve_iatas(to_city_raw) or [to_iata])

    from_city = from_city_raw or from_raw or from_iata
    to_city = to_city_raw or to_raw or to_iata
    return (from_iata, to_iata, from_city, to_city, date_str,
            from_iatas, to_iatas), None


def _iatas_in(items: list[dict]) -> set[str]:
    """Walks both flat flight rows (sec 1) and hub-grouped rows (secs 2/3)."""
    out: set[str] = set()
    for d in items:
        for k in ("depIata", "arrIata", "via"):
            v = d.get(k)
            if v:
                out.add(v)
        for leg in d.get("legs") or []:
            if leg.get("from"):
                out.add(leg["from"])
            if leg.get("to"):
                out.add(leg["to"])
        lay = d.get("layover") or {}
        if lay.get("airport"):
            out.add(lay["airport"])
        hub = d.get("hub") or {}
        if hub.get("iata"):
            out.add(hub["iata"])
        # bus_flight_bus pair-cards carry two hubs side-by-side instead of
        # the single `hub` field used by the existing hub-grouped sections.
        for hk in ("originHub", "destHub"):
            h2 = d.get(hk) or {}
            if h2.get("iata"):
                out.add(h2["iata"])
        for f in d.get("flightOptions") or []:
            for k in ("fromIata", "toIata"):
                v = f.get(k)
                if v:
                    out.add(v)
            for leg in f.get("legs") or []:
                if leg.get("from"):
                    out.add(leg["from"])
                if leg.get("to"):
                    out.add(leg["to"])
            for lay in f.get("layovers") or []:
                if lay.get("airport"):
                    out.add(lay["airport"])
    return out


def _airports_payload(*iata_sets: set[str]) -> dict:
    seen: set[str] = set()
    for s in iata_sets:
        seen.update(s)
    return {code: AIRPORT_COORDS[code] for code in seen if code in AIRPORT_COORDS}


@app.post("/api/flights")
def api_flights():
    payload = request.get_json(silent=True) or {}
    inputs, err = _resolve_inputs(payload)
    if err:
        return err
    from_iata, to_iata, from_city, to_city, date_str, from_iatas, to_iatas = inputs

    # SerpAPI accepts comma-separated IATAs in departure_id/arrival_id,
    # which Google Flights treats as "search across all of these airports".
    from_id = ",".join(from_iatas) if from_iatas else from_iata
    to_id = ",".join(to_iatas) if to_iatas else to_iata

    print("\n" + "=" * 60)
    print(f"✈️  /api/flights  {from_id} → {to_id}  date={date_str}")

    try:
        flights_df = flight_search(from_id, to_id, date_str)
        flight_err = None
    except Exception as e:
        app.logger.exception("flight_search failed")
        flights_df = pd.DataFrame()
        flight_err = str(e)
    flights_cached_at = flights_df.attrs.get("cached_at") if not flights_df.empty else None
    best_flights, cheap_flights = transform_flights(flights_df)
    print(f"   → best={len(best_flights)}  cheap={len(cheap_flights)}")

    iatas = _iatas_in(best_flights) | _iatas_in(cheap_flights) | {from_iata, to_iata}
    iatas |= set(from_iatas) | set(to_iatas)
    return jsonify({
        "bestFlights": best_flights,
        "cheapFlights": cheap_flights,
        "airports": _airports_payload(iatas),
        "resolved": {
            "from": from_iata, "to": to_iata,
            "fromCity": from_city, "toCity": to_city,
            "fromIatas": from_iatas, "toIatas": to_iatas,
        },
        "cachedAt": flights_cached_at,
        "error": flight_err,
    })


@app.post("/api/flight-plus-bus")
def api_flight_plus_bus():
    payload = request.get_json(silent=True) or {}
    inputs, err = _resolve_inputs(payload)
    if err:
        return err
    from_iata, to_iata, from_city, to_city, date_str, from_iatas, _to_iatas = inputs

    # Multi-airport origin: comma-join all IATAs of the origin city so SerpAPI
    # searches across both (e.g. MXP+LIN). sorted() keeps the cache key stable
    # regardless of resolve order. Single-airport origins keep the same IATA.
    from_id = ",".join(sorted(from_iatas)) if from_iatas else from_iata

    print("\n" + "=" * 60)
    print(f"🚌 /api/flight-plus-bus  {from_id} → {to_city}  date={date_str}")

    try:
        hubs_raw = find_cheap_flight_plus_ground_v2(
            departure_id=from_id,
            target_city=to_city,
            outbound_date=date_str,
            ground_date=date_str,
        )
        combo_err = None
    except Exception as e:
        app.logger.exception("find_cheap_flight_plus_ground_v2 failed")
        hubs_raw = []
        combo_err = str(e)
    hubs = transform_hubs(
        hubs_raw, date_str, from_iata, to_iata, from_city, to_city,
        mode="flight_plus_bus",
    )
    _enrich_hubs_with_trains(hubs, from_city, to_city, date_str, "flight_plus_bus")
    print(f"   → flightPlusBus hubs={len(hubs)}")

    iatas = _iatas_in(hubs) | {from_iata, to_iata}
    return jsonify({
        "flightPlusBus": hubs,
        "airports": _airports_payload(iatas),
        "error": combo_err,
    })


@app.post("/api/bus-plus-flight")
def api_bus_plus_flight():
    payload = request.get_json(silent=True) or {}
    inputs, err = _resolve_inputs(payload)
    if err:
        return err
    from_iata, to_iata, from_city, to_city, date_str, _from_iatas, to_iatas = inputs

    # Multi-airport destination: comma-join the destination IATAs so each hub
    # candidate's flight_search hits both arrival airports of the destination
    # city in a single SerpAPI call (e.g. MXP+LIN). Single-airport destinations
    # are unaffected.
    to_id = ",".join(sorted(to_iatas)) if to_iatas else to_iata

    print("\n" + "=" * 60)
    print(f"🚌✈ /api/bus-plus-flight  {from_city} → {to_id}  date={date_str}")

    try:
        hubs_raw = find_cheap_ground_plus_flight(
            departure_city=from_city,
            arrival_id=to_id,
            outbound_date=date_str,
            ground_date=date_str,
        )
        ground_err = None
    except Exception as e:
        app.logger.exception("find_cheap_ground_plus_flight failed")
        hubs_raw = []
        ground_err = str(e)
    hubs = transform_hubs(
        hubs_raw, date_str, from_iata, to_iata, from_city, to_city,
        mode="bus_plus_flight",
    )
    _enrich_hubs_with_trains(hubs, from_city, to_city, date_str, "bus_plus_flight")
    print(f"   → busPlusFlight hubs={len(hubs)}")

    iatas = _iatas_in(hubs) | {from_iata, to_iata}
    return jsonify({
        "busPlusFlight": hubs,
        "airports": _airports_payload(iatas),
        "error": ground_err,
    })


@app.post("/api/bus-flight-bus")
def api_bus_flight_bus():
    payload = request.get_json(silent=True) or {}
    inputs, err = _resolve_inputs(payload)
    if err:
        return err
    from_iata, to_iata, from_city, to_city, date_str, _from_iatas, _to_iatas = inputs

    print("\n" + "=" * 60)
    print(f"🚌✈🚌 /api/bus-flight-bus  {from_city} → {to_city}  date={date_str}")

    try:
        pairs_raw = find_cheap_bus_plus_flight_plus_bus(
            departure_city=from_city,
            arrival_city=to_city,
            outbound_date=date_str,
        )
        pipeline_err = None
    except Exception as e:
        app.logger.exception("find_cheap_bus_plus_flight_plus_bus failed")
        pairs_raw = []
        pipeline_err = str(e)
    pairs = transform_bus_flight_bus_pairs(pairs_raw, date_str, from_city, to_city)
    print(f"   → busFlightBus pairs={len(pairs)}")

    iatas = _iatas_in(pairs) | {from_iata, to_iata}
    return jsonify({
        "busFlightBus": pairs,
        "airports": _airports_payload(iatas),
        "error": pipeline_err,
    })


@app.post("/api/trains")
def api_trains():
    payload      = request.get_json(silent=True) or {}
    from_city    = (payload.get("from_city") or "").strip()
    to_city      = (payload.get("to_city") or "").strip()
    date_str     = (payload.get("date") or "").strip()

    if not from_city and payload.get("from_iata"):
        iata = payload["from_iata"].upper()
        from_city = AIRPORT_COORDS.get(iata, {}).get("city", iata)
    if not to_city and payload.get("to_iata"):
        iata = payload["to_iata"].upper()
        to_city = AIRPORT_COORDS.get(iata, {}).get("city", iata)

    if not from_city or not to_city:
        return jsonify({"error": "Both from_city and to_city are required."}), 400
    if not date_str:
        return jsonify({"error": "date is required (YYYY-MM-DD)."}), 400

    print("\n" + "=" * 60)
    print(f"🚌🚆 /api/trains  {from_city!r} → {to_city!r}  date={date_str}")

    with ThreadPoolExecutor(max_workers=2) as ex:
        train_future = ex.submit(train_get_trips, from_city, to_city, date_str)
        bus_future   = ex.submit(bus_direct_get_trips, from_city, to_city, date_str)

    train_err = None
    try:
        trains_df = train_future.result()
    except Exception as e:
        app.logger.exception("train_get_trips failed")
        trains_df = pd.DataFrame()
        train_err = str(e)
    try:
        buses_df = bus_future.result()
    except Exception as e:
        app.logger.exception("bus_direct_get_trips failed")
        buses_df = pd.DataFrame()

    results: list[dict] = []
    if trains_df is not None and not trains_df.empty:
        results += [_train_to_ui(r, date_str, from_city, to_city) for _, r in trains_df.iterrows()]
    if buses_df is not None and not buses_df.empty:
        results += [_bus_direct_to_ui(r, date_str, from_city, to_city) for _, r in buses_df.iterrows()]
    results.sort(key=lambda x: (0 if x.get("type") == "Train" else 1, x.get("price") or float("inf")))

    n_trains = sum(1 for r in results if r["type"] == "Train")
    n_buses  = sum(1 for r in results if r["type"] == "Bus")
    print(f"   → trains={n_trains}  buses={n_buses}")
    return jsonify({
        "trains":     results,
        "fromCity":   from_city,
        "toCity":     to_city,
        "date":       date_str,
        "fromCoords": _city_coords(from_city),
        "toCoords":   _city_coords(to_city),
        "error":      train_err,
    })


_AI_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["cheap", "fast", "best"]},
                    "id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "picks"],
    "additionalProperties": False,
}


@app.post("/api/ai-suggest")
def ai_suggest():
    payload = request.json or {}
    catalog = payload.get("catalog", [])
    lang = payload.get("lang", "en")

    if not catalog:
        return jsonify({"error": "catalog is empty"}), 400

    xai_key = os.getenv("XAI_API_KEY")
    if not xai_key:
        return jsonify({"error": "XAI_API_KEY not configured"}), 500

    if lang == "tr":
        system_msg = (
            "Sen bir seyahat asistanısın. Kullanıcıya sunulan tüm seyahat seçeneklerini "
            "analiz et ve en mantıklı 3 tanesini seç. Fiyat, toplam yolculuk süresi ve "
            "aktarma/bekleme süresi arasındaki dengeyi göz önünde bulundur. "
            "Özellikle fiyatta küçük bir fark varken layover süresi çok daha kısa olan "
            "seçenekleri yakala. Yanıtını Türkçe yaz."
        )
    else:
        system_msg = (
            "You are a travel assistant. Analyze all travel options and pick the 3 most "
            "worthwhile ones. Consider the trade-off between price, total trip duration, and "
            "connection/layover time. Especially catch options where a small price premium "
            "buys a significantly shorter layover. Reply in English."
        )

    lines = []
    for item in catalog:
        cat = item.get("cat", "")
        iid = item.get("id", "")
        if cat in ("Best Flight", "Cheapest Flight"):
            lines.append(
                f'[{iid}] {cat}: {item.get("airline")} {item.get("dep")}→{item.get("arr")} '
                f'{item.get("dur")} {item.get("stops", 0)} stops €{item.get("price")}'
            )
        elif cat in ("Flight+Bus", "Bus+Flight"):
            lines.append(
                f'[{iid}] {cat} via {item.get("hub")}: '
                f'flight {item.get("flightAirline")} {item.get("flightDep")}→{item.get("flightArr")} €{item.get("flightPrice")} | '
                f'bus {item.get("busDep")}→{item.get("busArr")} €{item.get("busPrice")} | '
                f'layover {item.get("layoverH")}h | total trip {item.get("totalTripH")}h | total €{item.get("totalPrice")}'
            )
        elif cat == "Bus/Train":
            lines.append(
                f'[{iid}] Bus/Train: {item.get("company")} {item.get("dep")}→{item.get("arr")} '
                f'{item.get("dur")} €{item.get("price")}'
            )

    user_msg = "Travel options:\n" + "\n".join(lines) + (
        "\n\nPick exactly 3 using 'cheap', 'fast', 'best' kinds. "
        "Use the exact bracketed ID for each pick. "
        "Keep reasons under 12 words."
    )

    try:
        resp = httpx.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {xai_key}", "Content-Type": "application/json"},
            json={
                "model": "grok-4-1-fast-non-reasoning-latest",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "travel_picks",
                        "strict": True,
                        "schema": _AI_SUGGEST_SCHEMA,
                    },
                },
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return jsonify(json.loads(content))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Port 5000 is taken by macOS AirPlay Receiver by default → use 5001.
    # threaded=True lets the 3 endpoints run in parallel under the dev server.
    app.run(host="127.0.0.1", port=5001, debug=True, threaded=True)
