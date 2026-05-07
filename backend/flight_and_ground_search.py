# travel_planner.py
import math
import json
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from dotenv import load_dotenv
# ========================= CONFIG =========================
load_dotenv()
GEOLOCATOR = Nominatim(user_agent="travel_planner_kerem")  # ONE instance

COUNTRY_EN = {"DE": "Germany", "IT": "Italy"}  # extend as needed

# ========================= DATA =========================
with open(Path(__file__).parent.parent / "data" / "filtered_airports_it_de.json") as f:
    airports_df = (
        pd.DataFrame.from_dict(json.load(f), orient="index")
        .query("country in ['DE', 'IT'] and iata != ''")
        [["iata", "name", "city", "country", "lat", "lon"]]
        .reset_index(drop=True)
    )

# ========================= HELPERS =========================
@dataclass
class Location:
    city: str
    country: str
    lat: float
    lon: float
    iata: str | None = None
    bus_name: str | None = None  # "City, Country" for CheckMyBus

@lru_cache(maxsize=100)  # ← caches repeated geocoding calls!
def geocode_city(city: str) -> tuple[float, float]:
    try:
        loc = GEOLOCATOR.geocode(city, exactly_one=True, timeout=10)
        if loc is None:
            raise ValueError(f"Could not geocode '{city}'")
        return loc.latitude, loc.longitude
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        raise ValueError(f"Geocoding error for '{city}': {e}") from e

def iata_to_location(iata: str) -> Location:
    row = airports_df[airports_df["iata"] == iata.upper()]
    if row.empty:
        raise ValueError(f"IATA {iata} not in airports.json")
    r = row.iloc[0]
    country = COUNTRY_EN.get(r["country"], r["country"])
    bus_name = f'{r["city"]}, {country}'
    return Location(city=r["city"], country=country, lat=r["lat"], lon=r["lon"], iata=iata, bus_name=bus_name)

# ========================= FLIGHTS =========================
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


