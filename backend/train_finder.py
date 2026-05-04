"""Train search: Germany (Deutsche Bahn REST) + Italy (Trenitalia via Playwright).

Interface mirrors flixbus_finder.get_trips() exactly so it can be used
as a drop-in alongside bus search in the hub pipeline:

    get_trips(origin, destination, date) -> pd.DataFrame

Returned DataFrame columns (same contract as flixbus_finder):
    origin, destination, date, departure_dt, arrival_dt,
    duration_min, price_eur, url, provider, stops

Country routing:
  Both cities in DE  → Deutsche Bahn REST (v6.db.transport.rest)
  Both cities in IT  → Trenitalia BFF via Playwright (lefrecce.it)
  Cross-border/other → empty DataFrame (too complex, use FlixBus)

Station ID caches (SQLite, auto-learned on first query):
  data/deutschland_stationen.db   (DE: query → station_id + name)
  data/italya_istasyonlar.db      (IT: id + name + search_terms)

Results are cached in train_cache (6h TTL) via data_cache.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote as _urlencode

import pandas as pd
import requests

from . import data_cache

# ── API endpoints ─────────────────────────────────────────────────────────────
_DB_REST        = "https://v6.db.transport.rest"
_TRENITALIA_HOME = "https://www.lefrecce.it/Channels.Website.WEB/"
_TRENITALIA_BFF  = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website"

# ── station ID caches ─────────────────────────────────────────────────────────
_DE_STATION_DB = Path(__file__).parent.parent / "data" / "deutschland_stationen.db"
_IT_STATION_DB = Path(__file__).parent.parent / "data" / "italya_istasyonlar.db"

_DE_STATION_DB.parent.mkdir(parents=True, exist_ok=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return (
        unicodedata.normalize("NFKD", s or "")
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )


def _norm_date(d: str) -> str | None:
    if not d:
        return None
    d = d.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


# ── country detection ─────────────────────────────────────────────────────────

def _build_city_sets() -> tuple[set[str], set[str]]:
    try:
        from .flight_and_ground_search import airports_df
        de = set(airports_df[airports_df["country"] == "DE"]["city"].str.lower().str.strip().dropna()) - {""}
        it = set(airports_df[airports_df["country"] == "IT"]["city"].str.lower().str.strip().dropna()) - {""}
    except Exception:
        de, it = set(), set()
    # Common English/German name variants not always in the airport list
    de |= {
        "munich", "munchen", "cologne", "koln", "nuremberg", "nurnberg",
        "frankfurt am main", "frankfurt", "dusseldorf", "hannover", "leipzig",
        "bremen", "dresden", "stuttgart", "berlin", "hamburg", "augsburg",
        "freiburg", "heidelberg", "karlsruhe", "mannheim", "kassel",
        "wiesbaden", "mainz", "erfurt", "kiel", "rostock", "lubeck",
        "saarbrucken", "magdeburg", "potsdam", "chemnitz", "halle",
        "oberhausen", "dortmund", "bochum", "duisburg", "essen",
    }
    it |= {
        "rome", "milan", "naples", "venice", "turin", "florence",
        "padua", "genoa", "naple", "bologna", "verona", "trieste",
        "bari", "catania", "palermo", "messina", "parma", "modena",
        "reggio emilia", "brescia", "livorno", "pisa", "siena",
        "ancona", "perugia", "trento", "bolzano", "udine", "ravenna",
        "vicenza", "treviso", "bergamo", "monza", "como", "lecce",
        "taranto", "brindisi", "pescara", "salerno", "cagliari", "foggia",
    }
    return de, it


_DE_CITIES, _IT_CITIES = _build_city_sets()


def _detect_country(origin: str, destination: str) -> str:
    """Return 'DE', 'IT', 'cross', or 'unknown'."""
    o, d = _norm(origin), _norm(destination)
    in_de = o in _DE_CITIES or d in _DE_CITIES
    in_it = o in _IT_CITIES or d in _IT_CITIES
    if in_de and not in_it:
        return "DE"
    if in_it and not in_de:
        return "IT"
    if in_de and in_it:
        return "cross"
    return "unknown"


# ── Germany: Deutsche Bahn REST ───────────────────────────────────────────────

def _init_de_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stationen (
            query      TEXT PRIMARY KEY,
            station_id TEXT NOT NULL,
            name       TEXT NOT NULL
        )
    """)
    conn.commit()


def _resolve_station_de(city: str) -> tuple[str, str] | None:
    """Return (station_id, station_name). Caches lookups in deutschland_stationen.db."""
    key = _norm(city)
    with sqlite3.connect(_DE_STATION_DB) as conn:
        _init_de_db(conn)
        row = conn.execute(
            "SELECT station_id, name FROM stationen WHERE query=?", (key,)
        ).fetchone()
    if row:
        return row  # (id, name)

    try:
        r = requests.get(
            f"{_DB_REST}/locations",
            params={"query": city, "results": 5, "stops": "true"},
            timeout=10,
        )
        r.raise_for_status()
        stops = [s for s in r.json() if s.get("type") in ("stop", "station")]
        if not stops:
            print(f"⚠️  DE station not found for {city!r}")
            return None
        best = stops[0]
        sid  = str(best["id"])
        name = best.get("name", city)
    except Exception as e:
        print(f"⚠️  DE station lookup failed for {city!r}: {e}")
        return None

    with sqlite3.connect(_DE_STATION_DB) as conn:
        _init_de_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO stationen (query, station_id, name) VALUES (?,?,?)",
            (key, sid, name),
        )
        conn.commit()
    return sid, name


def _get_trips_de(origin: str, destination: str, date: str) -> pd.DataFrame:
    from_st = _resolve_station_de(origin)
    to_st   = _resolve_station_de(destination)
    if not from_st or not to_st:
        return pd.DataFrame()

    from_id, from_name = from_st
    to_id,   to_name   = to_st
    date_iso = _norm_date(date)
    if not date_iso:
        return pd.DataFrame()

    try:
        r = requests.get(
            f"{_DB_REST}/journeys",
            params={
                "from":            from_id,
                "to":              to_id,
                "departure":       f"{date_iso}T06:00:00",
                "results":         15,
                "tickets":         "true",
                "nationalExpress": "true",
                "national":        "true",
                "regionalExp":     "true",
                "regional":        "true",
                "suburban":        "false",
                "subway":          "false",
                "tram":            "false",
                "bus":             "false",
                "ferry":           "false",
                "taxi":            "false",
            },
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"⚠️  DB REST journeys failed {from_name}→{to_name}: {e}")
        return pd.DataFrame()

    rows = []
    for j in r.json().get("journeys", []):
        legs = j.get("legs", [])
        if not legs:
            continue
        dep = _parse_dt(legs[0].get("departure"))
        arr = _parse_dt(legs[-1].get("arrival"))
        if dep is None or arr is None:
            continue
        duration_min = int((arr - dep).total_seconds() / 60)
        price_obj = j.get("price")
        price = _safe_float(price_obj.get("amount")) if price_obj else None
        # Count actual train-leg transfers (legs with a line = train segments)
        train_legs = [l for l in legs if l.get("line")]
        stops_count = max(0, len(train_legs) - 1)
        url = (
            f"https://www.bahn.de/buchung/start#sts=true"
            f"&so={_urlencode(from_name)}&zo={_urlencode(to_name)}"
            f"&sod={date_iso.replace('-', '.')}"
        )
        rows.append({
            "origin":       from_name,
            "destination":  to_name,
            "date":         date_iso,
            "departure_dt": dep,
            "arrival_dt":   arr,
            "duration_min": duration_min,
            "price_eur":    price,
            "url":          url,
            "provider":     "DB",
            "stops":        stops_count,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["price_eur"].notna()].sort_values("price_eur").reset_index(drop=True)
    print(f"✅ DE train search: {len(df)} results {from_name}→{to_name}")
    return df


# ── Italy: Trenitalia via Playwright ─────────────────────────────────────────

# English city name → Italian canonical form used in Trenitalia station names
_IT_NAME_MAP: dict[str, str] = {
    "rome": "roma", "milan": "milano", "naples": "napoli",
    "venice": "venezia", "florence": "firenze", "genoa": "genova",
    "turin": "torino", "padua": "padova", "bologna": "bologna",
    "verona": "verona", "trieste": "trieste", "bari": "bari",
    "catania": "catania", "palermo": "palermo", "messina": "messina",
}

# Preferred main-station keywords per city (matched case-insensitively in isim)
_IT_PREFERRED: dict[str, str] = {
    "roma":    "Roma Termini",
    "milano":  "Milano Centrale",
    "venezia": "Venezia S. Lucia",
    "firenze": "Firenze S. M. Novella",
    "bologna": "Bologna Centrale",
    "napoli":  "Napoli Centrale",
    "torino":  "Torino Porta Nuova",
    "verona":  "Verona Porta Nuova",
    "genova":  "Genova Piazza Principe",
    "padova":  "Padova",
    "trieste": "Trieste Centrale",
    "bari":    "Bari Centrale",
    "palermo": "Palermo Centrale",
}


def _init_it_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS istasyonlar (
            id           INTEGER PRIMARY KEY,
            isim         TEXT NOT NULL,
            search_terms TEXT
        )
    """)
    # Gracefully add search_terms to pre-existing DBs from istasyon_kazici.py
    try:
        conn.execute("ALTER TABLE istasyonlar ADD COLUMN search_terms TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _find_station_it(city: str, page) -> tuple[int, str] | None:
    """Return (station_id, station_name). Caches in italya_istasyonlar.db.

    Resolution order:
      1. Preferred main station (e.g. 'Roma Termini' for 'roma')
      2. Station whose name starts with the city name
      3. Any station whose name or search_terms contain the city name
      4. Trenitalia API via Playwright (result is then cached)
    """
    # Translate English city names to Italian
    raw_key = _norm(city)
    key = _IT_NAME_MAP.get(raw_key, raw_key)  # e.g. "venice" → "venezia"

    with sqlite3.connect(_IT_STATION_DB) as conn:
        _init_it_db(conn)

        # 1. Preferred main station
        preferred = _IT_PREFERRED.get(key)
        if preferred:
            row = conn.execute(
                "SELECT id, isim FROM istasyonlar WHERE LOWER(isim)=? LIMIT 1",
                (preferred.lower(),),
            ).fetchone()
            if row:
                return int(row[0]), row[1]

        # 2. Station name starts with city name (e.g. "venezia s. lucia" for "venezia")
        row = conn.execute(
            "SELECT id, isim FROM istasyonlar WHERE LOWER(isim) LIKE ? LIMIT 1",
            (f"{key} %",),
        ).fetchone()
        if row:
            return int(row[0]), row[1]

        # 3. Exact city match or search_terms alias
        row = conn.execute(
            "SELECT id, isim FROM istasyonlar "
            "WHERE LOWER(isim) LIKE ? OR LOWER(search_terms) LIKE ? LIMIT 1",
            (f"%{key}%", f"%{key}%"),
        ).fetchone()
    if row:
        return int(row[0]), row[1]

    # Not in DB → ask Trenitalia API via the already-open Playwright page
    js = """
    async (name) => {
        const r = await fetch(
            `""" + _TRENITALIA_BFF + """/locations/search?name=${encodeURIComponent(name)}&limit=5`
        );
        if (!r.ok) return [];
        return await r.json();
    }
    """
    try:
        results = page.evaluate(js, city)
    except Exception as e:
        print(f"⚠️  IT station lookup failed for {city!r}: {e}")
        return None

    if not results:
        return None

    best         = results[0]
    station_id   = int(best["id"])
    station_name = best.get("name", city)

    with sqlite3.connect(_IT_STATION_DB) as conn:
        _init_it_db(conn)
        conn.execute(
            "INSERT OR IGNORE INTO istasyonlar (id, isim, search_terms) VALUES (?,?,?)",
            (station_id, station_name, key),
        )
        conn.commit()

    return station_id, station_name


def _parse_trenitalia_solution(
    sol: dict, from_name: str, to_name: str, date_iso: str, url: str
) -> dict | None:
    inner     = sol.get("solution", sol)
    price_obj = inner.get("price") or {}
    price     = _safe_float(price_obj.get("amount"))

    dep = _parse_dt(inner.get("departureTime"))
    arr = _parse_dt(inner.get("arrivalTime"))
    if dep is None or arr is None:
        return None

    duration_min = int((arr - dep).total_seconds() / 60) if arr > dep else None
    trains       = inner.get("trains") or inner.get("nodes") or []
    stops_count  = max(0, len(trains) - 1)

    return {
        "origin":       from_name,
        "destination":  to_name,
        "date":         date_iso,
        "departure_dt": dep,
        "arrival_dt":   arr,
        "duration_min": duration_min,
        "price_eur":    price,
        "url":          url,
        "provider":     "Trenitalia",
        "stops":        stops_count,
    }


def _get_trips_it(origin: str, destination: str, date: str) -> pd.DataFrame:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("⚠️  playwright not installed — Italian train search unavailable")
        return pd.DataFrame()

    date_iso = _norm_date(date)
    if not date_iso:
        return pd.DataFrame()

    rows: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        try:
            page.goto(_TRENITALIA_HOME, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2_000)

            from_st = _find_station_it(origin, page)
            to_st   = _find_station_it(destination, page)
            if not from_st or not to_st:
                return pd.DataFrame()

            from_id, from_name = from_st
            to_id,   to_name   = to_st

            booking_url = (
                f"https://www.lefrecce.it/Channels.Website.WEB/"
                f"#/search?departureStationId={from_id}"
                f"&arrivalStationId={to_id}"
                f"&departureDate={date_iso}&adults=1"
            )

            body = {
                "departureLocationId": from_id,
                "arrivalLocationId":   to_id,
                "departureTime":       f"{date_iso}T08:00:00.000+01:00",
                "adults":  1,
                "children": 0,
                "criteria": {
                    "frecceOnly":   False,
                    "regionalOnly": False,
                    "noChanges":    False,
                    "order":        "DEPARTURE_DATE",
                    "limit":        15,
                    "offset":       0,
                },
                "advancedSearchRequest": {"bestFare": False},
            }

            js_post = """
            async (body) => {
                const r = await fetch('""" + _TRENITALIA_BFF + """/ticket/solutions', {
                    method:  'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept':       'application/json'
                    },
                    body: JSON.stringify(body)
                });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return await r.json();
            }
            """
            data = page.evaluate(js_post, body)

            for sol in data.get("solutions", []):
                parsed = _parse_trenitalia_solution(
                    sol, from_name, to_name, date_iso, booking_url
                )
                if parsed:
                    rows.append(parsed)

        except Exception as e:
            print(f"⚠️  Trenitalia search {origin}→{destination} failed: {e}")
        finally:
            browser.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df[df["price_eur"].notna()].sort_values("price_eur").reset_index(drop=True)
    print(f"✅ IT train search: {len(df)} results {from_name}→{to_name}")
    return df


# ── public interface ──────────────────────────────────────────────────────────

def get_trips(origin: str, destination: str, date: str) -> pd.DataFrame:
    """Search trains origin→destination on date. Returns DataFrame compatible
    with flixbus_finder.get_trips() for use in the hub pipeline."""
    cached = data_cache.train_get(origin, destination, date)
    if cached is not None:
        return cached

    country = _detect_country(origin, destination)
    print(f"🚆 Train search: {origin!r} → {destination!r}  [{country}]  date={date}")

    if country == "DE":
        df = _get_trips_de(origin, destination, date)
    elif country == "IT":
        df = _get_trips_it(origin, destination, date)
    else:
        print(f"   ⏭  Cross-border or unknown country pair — train search skipped")
        return pd.DataFrame()

    if not df.empty:
        data_cache.train_set(origin, destination, date, df)

    return df
