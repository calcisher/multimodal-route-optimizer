# travel_planner.py
import math
import json
from functools import lru_cache
from dataclasses import dataclass
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import os
import serpapi
from dotenv import load_dotenv
# ========================= CONFIG =========================
load_dotenv()
API_KEY = os.getenv("SERPAPI_KEY")  # ← put in .env, never hardcode
GEOLOCATOR = Nominatim(user_agent="travel_planner_kerem")  # ONE instance

COUNTRY_EN = {"DE": "Germany", "IT": "Italy"}  # extend as needed

# ========================= DATA =========================
with open("./filtered_airports_it_de.json") as f:  # put json next to script or use absolute path
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

@lru_cache(maxsize=100)
def city_en_from_coords(lat: float, lon: float, country_code: str, iata: str = "") -> str:
    """Reverse geocode → perfect CheckMyBus string (English)."""
    country = COUNTRY_EN.get(country_code, country_code)
    loc = GEOLOCATOR.reverse((lat, lon), language="en", addressdetails=True, exactly_one=True, timeout=10)
    if not loc:
        raise ValueError(f"Reverse geocode failed for ({lat}, {lon})")
    addr = loc.raw.get("address", {})
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county")
    if not city:
        raise ValueError(f"No city in address for ({lat}, {lon})")
    return f"{city}, {country}"

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

def explore_search(departure_id: str, outbound_date: str, target_city: str, max_distance_km: int = 300):
    """Cheap flights to cities near your target + IATA + bus-ready name."""
    lat, lon = geocode_city(target_city)
    target = {"lat": lat, "lon": lon}

    client = serpapi.Client(api_key=API_KEY)
    results = client.search({
        "engine": "google_travel_explore",
        "departure_id": departure_id,
        "currency": "EUR",
        "type": "2",
        "outbound_date": outbound_date,
        "no_cache": False,
    })

    rows = []
    for dest in results.get("destinations", []):
        if "flight_price" not in dest:
            continue
        iata = dest.get("destination_airport", {}).get("code")  # ← NEW!
        lat_d, lon_d = dest["gps_coordinates"]["latitude"], dest["gps_coordinates"]["longitude"]
        distance = haversine_km(lat_d, lon_d, target["lat"], target["lon"])

        if distance > max_distance_km:
            continue

        # Build clean bus name (prefer reverse geocode on GPS)
        try:
            bus_name = city_en_from_coords(lat_d, lon_d, country_code=dest.get("country", "")[:2] or "DE", iata=iata or "")
        except Exception:
            # fallback
            bus_name = f"{dest['name']}, {dest.get('country', 'Unknown')}"

        rows.append({
            "city": dest["name"],
            "country": dest.get("country"),
            "iata": iata,
            "price": dest["flight_price"],
            "duration": dest["flight_duration"],
            "airline": dest["airline"],
            "link": dest["link"],
            "lat": lat_d,
            "lon": lon_d,
            "distance_km": round(distance, 1),
            "bus_departure_name": bus_name,   # ← ready for CheckMyBus
        })

    df = pd.DataFrame(rows).sort_values("price").reset_index(drop=True)
    return df

# ========================= BUS / TRAIN =========================
from checkmybus import CheckMyBusClient, CheckMyBusSearchParams
from flixbus_finder import get_trips

def bus_train_transfer(departure_location: str, arrival_location: str, departure_date: str):
    """Return DataFrame of buses/trains. departure_location must be clean 'City, Country'."""
    client = CheckMyBusClient()

    print(f"🔎 Bus/Train search STARTING: {departure_location} → {arrival_location} on {departure_date}")

    try:
        search_result = client.search(CheckMyBusSearchParams(
            departure_location=departure_location,
            arrival_location=arrival_location,
            departure_date=departure_date,
        ))

        df = search_result.to_dataframe()          # renamed for clarity

        # Count buses and trains safely (in case column is missing)
        if "transport_mode" in df.columns:
            count_bus = len(df[df["transport_mode"] == "bus"])
            count_train = len(df[df["transport_mode"] == "train"])
        else:
            count_bus = count_train = 0
            print("⚠️  'transport_mode' column not found in result!")

        print(f"✅ Search DONE for {departure_location}. "
              f"Total: {len(df)} results → {count_bus} bus(es), {count_train} train(s)")

        if df.empty:
            print(f"⚠️  No buses or trains found from {departure_location} to {arrival_location}")

        return df

    except Exception as e:   # catches API errors, bad parameters, network issues, etc.
        print(f"❌ ERROR searching {departure_location} → {arrival_location}: {e}")
        # You can raise or return empty DataFrame depending on how strict you want it
        return pd.DataFrame()   # empty DF so the main pipeline doesn't crash

# ========================= MAIN PIPELINE (the simple way) =========================
def find_cheap_flight_plus_ground(departure_id: str,
                                  target_city: str,          # e.g. "Munich, Germany" or just "Munich"
                                  outbound_date: str,        # "2026-05-16"
                                  ground_date: str = None,   # defaults to same day
                                  max_distance_km: int = 300):
    if ground_date is None:
        ground_date = outbound_date

    # 1. Get cheap flights near target
    nearby = explore_search(departure_id, outbound_date, target_city, max_distance_km)
    if nearby is not None:
        print(f"Hey good news! Found {len(nearby)} nearby flights.")
    else:
        print("Oh sorry! There is no nearby flights.")
    for _, row in nearby.iterrows():
        print(f"From {row["city"]} and price is {row["price"]}")

    # 2. For each, search ground transport
    combined = []
    for _, row in nearby.iterrows():
        try:
            dep_city = row["bus_departure_name"].split(",")[0].strip()
            arr_city = target_city.split(",")[0].strip()

            ground_df = get_trips(dep_city, arr_city, ground_date)
            if ground_df.empty:
                continue

            best = ground_df.loc[ground_df["price_eur"].idxmin()]
            combined.append({
                **row.to_dict(),
                "ground_type": "Bus",
                "ground_price": best["price_eur"],
                "total_price": row["price"] + best["price_eur"],
                "ground_duration": best["duration_min"],
                "ground_link": best["url"],
                "ground_departure": best["departure_dt"],
                "ground_arrival": best["arrival_dt"],
            })
        except Exception as e:
            print(f"Bus search failed for {row['city']}: {e}")

    if combined:
        result_df = pd.DataFrame(combined).sort_values("total_price").reset_index(drop=True)
        return result_df[["city", "iata", "price", "ground_type", "ground_price", "total_price",
                          "distance_km", "bus_departure_name", "link",
                          "ground_duration", "ground_departure", "ground_arrival"]]
    else:
        return pd.DataFrame()  # empty

