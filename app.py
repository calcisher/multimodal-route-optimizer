"""Flask backend that wires the existing React UI to flight_search and find_cheap_flight_plus_ground."""
from __future__ import annotations

import json
import math
import unicodedata
from datetime import date as date_cls, datetime
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

from bus_and_flight_search import _to_naive, find_cheap_ground_plus_flight
from flight_and_ground_search import airports_df, geocode_city, haversine_km
from flight_plus_bus_search import find_cheap_flight_plus_ground_v2
from flight_search import flight_search

# IT/DE airport list — drives the autocomplete and the nearby-airports search.
with open(Path(__file__).parent / "filtered_airports_it_de.json") as _f:
    _ALL = json.load(_f)

# Global IATA → coords lookup. The flight responses we return reference any
# IATA SerpAPI surfaces as a layover or via-hub (PMI, IST, STR, …) — those
# aren't in the IT/DE list, but the UI map still needs lat/lon to plot them.
# Falls back gracefully if the global file isn't checked out (it's a 9MB blob
# kept out of git on some setups).
_GLOBAL_FILE = Path(__file__).parent / "airports.json"
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

AIRPORT_COORDS: dict[str, dict] = {}
# Filtered file wins on conflict (curated) — global fills the gaps.
for row in _GLOBAL_ALL.values():
    iata = row.get("iata")
    if iata and "lat" in row and "lon" in row:
        AIRPORT_COORDS[iata] = {
            "lat": row["lat"],
            "lon": row["lon"],
            "city": _CITY_OVERRIDES.get(iata, row.get("city", "")),
            "country": row.get("country", ""),
        }
for row in _ALL.values():
    iata = row.get("iata")
    if iata:
        AIRPORT_COORDS[iata] = {
            "lat": row["lat"],
            "lon": row["lon"],
            "city": _CITY_OVERRIDES.get(iata, row.get("city", "")),
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


# ---------- helpers ----------

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


def transform_flights(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Split one flight_search DataFrame into (bestFlights, cheapFlights)."""
    if df is None or df.empty:
        return [], []

    best_df = df[df["flight_type"] == "Best"].copy()
    other_df = df[df["flight_type"] != "Best"].copy()

    best = [_flight_row_to_ui(r) for _, r in best_df.sort_values("price").iterrows()]
    cheap = [
        _flight_row_to_ui(r)
        for _, r in other_df.sort_values("price").head(8).iterrows()
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
        })
    return out


# ---------- routes ----------

@app.get("/")
def index():
    return send_from_directory(UI_DIR, "Multi Route.html")


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
            "city": r.get("city"),
            "country": r.get("country"),
        }
        for r in _ALL.values()
        if r.get("iata") and r.get("city")
    ]
    return {"airports": rows}


def _resolve_inputs(payload: dict):
    """Returns ((from_iata, to_iata, from_city, to_city, date_str), None)
    on success, or (None, (response, status)) on failure."""
    from_raw = (payload.get("from_iata") or payload.get("from_city") or "").strip()
    to_raw = (payload.get("to_iata") or payload.get("to_city") or "").strip()
    from_city_raw = (payload.get("from_city") or "").strip()
    to_city_raw = (payload.get("to_city") or "").strip()
    date_str = (payload.get("date") or "").strip()

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

    from_city = from_city_raw or from_raw or from_iata
    to_city = to_city_raw or to_raw or to_iata
    return (from_iata, to_iata, from_city, to_city, date_str), None


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
    from_iata, to_iata, from_city, to_city, date_str = inputs

    print("\n" + "=" * 60)
    print(f"✈️  /api/flights  {from_iata} → {to_iata}  date={date_str}")

    try:
        flights_df = flight_search(from_iata, to_iata, date_str)
        flight_err = None
    except Exception as e:
        app.logger.exception("flight_search failed")
        flights_df = pd.DataFrame()
        flight_err = str(e)
    best_flights, cheap_flights = transform_flights(flights_df)
    print(f"   → best={len(best_flights)}  cheap={len(cheap_flights)}")

    iatas = _iatas_in(best_flights) | _iatas_in(cheap_flights) | {from_iata, to_iata}
    return jsonify({
        "bestFlights": best_flights,
        "cheapFlights": cheap_flights,
        "airports": _airports_payload(iatas),
        "resolved": {
            "from": from_iata, "to": to_iata,
            "fromCity": from_city, "toCity": to_city,
        },
        "error": flight_err,
    })


@app.post("/api/flight-plus-bus")
def api_flight_plus_bus():
    payload = request.get_json(silent=True) or {}
    inputs, err = _resolve_inputs(payload)
    if err:
        return err
    from_iata, to_iata, from_city, to_city, date_str = inputs

    print("\n" + "=" * 60)
    print(f"🚌 /api/flight-plus-bus  {from_iata} → {to_city}  date={date_str}")

    try:
        hubs_raw = find_cheap_flight_plus_ground_v2(
            departure_id=from_iata,
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
    from_iata, to_iata, from_city, to_city, date_str = inputs

    print("\n" + "=" * 60)
    print(f"🚌✈ /api/bus-plus-flight  {from_city} → {to_iata}  date={date_str}")

    try:
        hubs_raw = find_cheap_ground_plus_flight(
            departure_city=from_city,
            arrival_id=to_iata,
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
    print(f"   → busPlusFlight hubs={len(hubs)}")

    iatas = _iatas_in(hubs) | {from_iata, to_iata}
    return jsonify({
        "busPlusFlight": hubs,
        "airports": _airports_payload(iatas),
        "error": ground_err,
    })


if __name__ == "__main__":
    # Port 5000 is taken by macOS AirPlay Receiver by default → use 5001.
    # threaded=True lets the 3 endpoints run in parallel under the dev server.
    app.run(host="127.0.0.1", port=5001, debug=True, threaded=True)
