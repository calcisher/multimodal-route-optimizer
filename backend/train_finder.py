"""Train search via Deutsche Bahn REST (v6.db.transport.rest).

Interface mirrors flixbus_finder.get_trips() exactly so it can be used
as a drop-in alongside bus search in the hub pipeline:

    get_trips(origin, destination, date) -> pd.DataFrame

Returned DataFrame columns (same contract as flixbus_finder):
    origin, destination, date, departure_dt, arrival_dt,
    duration_min, price_eur, url, provider, stops

Country routing — all handled by DB REST (HAFAS):
  DE city involved → "DE"       (German domestic + all cross-border)
  Both cities IT   → "DB_EUROPE" (DB covers EC/ICE/Nightjet into Italy)
  Other known EU   → "DB_EUROPE"
  Unknown pair     → skip (empty DataFrame)

Station ID cache (SQLite, auto-learned on first query):
  data/deutschland_stationen.db  (query → station_id + name + lat + lon)

Results are cached in train_cache (6h TTL) via data_cache.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote as _urlencode

import time

import pandas as pd
import requests

from . import data_cache

# ── API endpoints ─────────────────────────────────────────────────────────────
_DB_REST = "https://v6.db.transport.rest"

# ── station ID cache ──────────────────────────────────────────────────────────
_DE_STATION_DB = Path(__file__).parent.parent / "data" / "deutschland_stationen.db"

_DE_STATION_DB.parent.mkdir(parents=True, exist_ok=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def _http_get(url: str, params: dict, timeout: int = 15, retries: int = 2) -> requests.Response:
    """GET with retry on timeout / 5xx; raises on final failure."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"Accept": "application/json"})
            if r.status_code < 500:
                return r
            last_exc = requests.HTTPError(f"{r.status_code} {r.reason}", response=r)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        if attempt < retries - 1:
            time.sleep(2 ** attempt)  # 1s, 2s
    raise last_exc  # type: ignore[misc]


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

# European cities reachable by DB REST (cross-border ICE/EC/Nightjet).
# Used when neither city is German but DB REST still covers the route.
_DB_EUROPE_CITIES: set[str] = {
    # Austria
    "vienna", "wien", "salzburg", "innsbruck", "graz", "linz", "klagenfurt",
    # Switzerland
    "zurich", "zuerich", "basel", "bern", "geneva", "genf",
    "lausanne", "lucerne", "luzern", "lugano", "interlaken",
    # Netherlands
    "amsterdam", "rotterdam", "the hague", "den haag", "utrecht", "eindhoven",
    # Belgium
    "brussels", "brussel", "bruxelles", "antwerp", "antwerpen",
    "ghent", "gent", "liege", "bruges", "brugge",
    # Czech Republic
    "prague", "prag", "praha", "brno",
    # France
    "paris", "strasbourg", "lyon", "marseille", "nice", "lille",
    # Denmark
    "copenhagen", "kobenhavn",
    # Luxembourg
    "luxembourg",
    # Hungary
    "budapest",
    # Poland
    "warsaw", "warszawa", "krakow", "wroclaw", "gdansk", "poznan",
    # Slovenia / Croatia / Slovakia
    "ljubljana", "zagreb", "bratislava",
    # Italy — DB REST covers EC/ICE/Nightjet into/within Italy
    "milan", "milano", "rome", "roma", "naples", "napoli",
    "venice", "venezia", "turin", "torino", "florence", "firenze",
    "genoa", "genova", "bologna", "verona", "trieste",
    "padua", "padova", "bari", "catania", "palermo", "messina",
    "parma", "modena", "brescia", "livorno", "pisa", "siena",
    "ancona", "perugia", "trento", "bolzano", "udine", "ravenna",
    "vicenza", "treviso", "bergamo", "monza", "como",
    "lecce", "taranto", "brindisi", "pescara", "salerno",
    "cagliari", "foggia", "reggio calabria", "reggio emilia",
}


def _build_city_sets() -> tuple[set[str], set[str]]:
    try:
        from .flight_and_ground_search import airports_df
        de = set(airports_df[airports_df["country"] == "DE"]["city"].str.lower().str.strip().dropna()) - {""}
        it = set(airports_df[airports_df["country"] == "IT"]["city"].str.lower().str.strip().dropna()) - {""}
    except Exception:
        de, it = set(), set()
    de |= {
        # Large cities (English + German spellings)
        "munich", "munchen", "cologne", "koln", "nuremberg", "nurnberg",
        "frankfurt am main", "frankfurt", "dusseldorf", "hannover", "leipzig",
        "bremen", "dresden", "stuttgart", "berlin", "hamburg", "augsburg",
        # Medium cities
        "freiburg", "heidelberg", "karlsruhe", "mannheim", "kassel",
        "wiesbaden", "mainz", "erfurt", "kiel", "rostock", "lubeck",
        "saarbrucken", "magdeburg", "potsdam", "chemnitz", "halle",
        "oberhausen", "dortmund", "bochum", "duisburg", "essen",
        # Additional cities
        "bonn", "aachen", "wuppertal", "bielefeld", "munster", "paderborn",
        "darmstadt", "regensburg", "ingolstadt", "wurzburg", "ulm",
        "braunschweig", "oldenburg", "osnabruck", "gelsenkirchen",
        "leverkusen", "wolfsburg", "hildesheim", "gottingen",
        "bamberg", "bayreuth", "passau", "landshut", "rosenheim",
        "konstanz", "constance", "flensburg", "trier", "koblenz",
        "jena", "gera", "siegen", "hagen", "hamm", "solingen", "mulheim",
    }
    it |= {
        # English names
        "rome", "milan", "naples", "venice", "turin", "florence",
        "padua", "genoa", "bologna", "verona", "trieste",
        "bari", "catania", "palermo", "messina", "parma", "modena",
        "brescia", "livorno", "pisa", "siena", "ancona", "perugia",
        "trento", "bolzano", "udine", "ravenna", "vicenza", "treviso",
        "bergamo", "monza", "como", "lecce", "taranto", "brindisi",
        "pescara", "salerno", "cagliari", "foggia",
        # Italian names
        "roma", "milano", "napoli", "venezia", "torino", "firenze",
        "padova", "genova", "reggio emilia", "reggio calabria",
    }
    return de, it


_DE_CITIES, _IT_CITIES = _build_city_sets()


def _detect_country(origin: str, destination: str) -> str:
    """Return 'DE', 'DB_EUROPE', or 'unknown'. All routes use DB REST.

    'DE'        → German city involved (domestic + all cross-border)
    'DB_EUROPE' → Both cities known European (incl. Italian domestic)
    'unknown'   → skip
    """
    o, d = _norm(origin), _norm(destination)
    if o in _DE_CITIES or d in _DE_CITIES:
        return "DE"
    if (o in _IT_CITIES and d in _IT_CITIES) or \
       (o in _DB_EUROPE_CITIES or d in _DB_EUROPE_CITIES):
        return "DB_EUROPE"
    return "unknown"


# ── Germany: Deutsche Bahn REST ───────────────────────────────────────────────

def _init_de_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stationen (
            query      TEXT PRIMARY KEY,
            station_id TEXT NOT NULL,
            name       TEXT NOT NULL,
            lat        REAL,
            lon        REAL
        )
    """)
    for col in ("lat", "lon"):
        try:
            conn.execute(f"ALTER TABLE stationen ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _resolve_station_de(city: str) -> tuple[str, str, float | None, float | None] | None:
    """Return (station_id, station_name, lat, lon). Caches in deutschland_stationen.db."""
    key = _norm(city)
    with sqlite3.connect(_DE_STATION_DB) as conn:
        _init_de_db(conn)
        row = conn.execute(
            "SELECT station_id, name, lat, lon FROM stationen WHERE query=?", (key,)
        ).fetchone()
    if row:
        return row  # (id, name, lat, lon)

    try:
        r = _http_get(
            f"{_DB_REST}/locations",
            params={"query": city, "results": 5, "stops": "true"},
        )
        r.raise_for_status()
        stops = [s for s in r.json() if s.get("type") in ("stop", "station")]
        if not stops:
            print(f"⚠️  DE station not found for {city!r}")
            return None
        best = stops[0]
        sid  = str(best["id"])
        name = best.get("name", city)
        loc  = best.get("location") or {}
        lat  = loc.get("latitude")
        lon  = loc.get("longitude")
    except Exception as e:
        print(f"⚠️  DE station lookup failed for {city!r}: {e}")
        return None

    with sqlite3.connect(_DE_STATION_DB) as conn:
        _init_de_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO stationen (query, station_id, name, lat, lon) VALUES (?,?,?,?,?)",
            (key, sid, name, lat, lon),
        )
        conn.commit()
    return sid, name, lat, lon


def _db_soid(sid: str, name: str, lat: float | None, lon: float | None) -> str:
    """Build DB HAFAS station ID string for bahn.de deep-link URLs.

    The name is kept as plain text here; the caller URL-encodes the entire
    soid string once via _urlencode(soid).  Encoding the name here first
    would cause double-encoding for stations with umlauts or parentheses
    (e.g. FRANKFURT(MAIN), MUNICH (MÜNCHEN)) and break the bahn.de link.
    """
    x = int(round(lon * 1_000_000)) if lon is not None else 0
    y = int(round(lat * 1_000_000)) if lat is not None else 0
    return f"A=1@O={name}@X={x}@Y={y}@U=80@L={sid}@"


def _get_trips_de(origin: str, destination: str, date: str) -> pd.DataFrame:
    from_st = _resolve_station_de(origin)
    to_st   = _resolve_station_de(destination)
    if not from_st or not to_st:
        return pd.DataFrame()

    from_id, from_name, from_lat, from_lon = from_st
    to_id,   to_name,   to_lat,   to_lon   = to_st
    date_iso = _norm_date(date)
    if not date_iso:
        return pd.DataFrame()

    try:
        r = _http_get(
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

        # Extract per-leg waypoints (station name + coordinates) for map display
        waypoints = []
        for leg in train_legs:
            o = leg.get("origin") or {}
            loc = o.get("location") or {}
            waypoints.append({
                "name": o.get("name", ""),
                "lat":  loc.get("latitude"),
                "lon":  loc.get("longitude"),
            })
        if train_legs:
            last_dest = train_legs[-1].get("destination") or {}
            dest_loc  = last_dest.get("location") or {}
            waypoints.append({
                "name": last_dest.get("name", ""),
                "lat":  dest_loc.get("latitude"),
                "lon":  dest_loc.get("longitude"),
            })

        soid = _db_soid(from_id, from_name, from_lat, from_lon)
        zoid = _db_soid(to_id,   to_name,   to_lat,   to_lon)
        url = (
            f"https://int.bahn.de/en/buchung/fahrplan/suche#sts=true"
            f"&so={_urlencode(from_name)}&zo={_urlencode(to_name)}"
            f"&kl=2&soid={_urlencode(soid)}&zoid={_urlencode(zoid)}"
            f"&hd={dep.strftime('%Y-%m-%dT%H:%M:%S')}"
            f"&hza=D&hz=[]&ar=false&s=true&d=false"
            f"&vm=00,01,02,03,04,05,06,07,08,09&fm=false&bp=false"
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
            "waypoints":    waypoints,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["price_eur"].notna()].copy()  # Drop results with no price info
    if df.empty:
        return df
    df = df.sort_values("price_eur", key=lambda s: s.fillna(float("inf"))).reset_index(drop=True)
    print(f"✅ DE train search: {len(df)} results {from_name}→{to_name}")
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

    if country in ("DE", "DB_EUROPE"):
        df = _get_trips_de(origin, destination, date)
    else:
        print(f"   ⏭  Unknown city pair — train search skipped")
        return pd.DataFrame()

    if not df.empty:
        data_cache.train_set(origin, destination, date, df)

    return df
