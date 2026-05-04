# ground_first_search.py
import math
import json
import os
import time
import sqlite3
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import pandas as pd
import requests
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import serpapi
from dotenv import load_dotenv
from .checkmybus import CheckMyBusClient, CheckMyBusSearchParams

# ========================= CONFIG & DATA =========================
load_dotenv()
API_KEY = os.getenv("SERPAPI_KEY")

GEOLOCATOR = Nominatim(user_agent="travel_planner_eser_v7")
COUNTRY_EN = {"DE": "Germany", "IT": "Italy"}

# FlixBus Headers
FLIXBUS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

with open(Path(__file__).parent.parent / "data" / "airports.json") as f:
    airports_df = (
        pd.DataFrame.from_dict(json.load(f), orient="index")
        .query("country in ['DE', 'IT'] and iata != ''")
        [["iata", "name", "city", "country", "lat", "lon"]]
        .reset_index(drop=True)
    )


# ========================= HELPERS =========================
@lru_cache(maxsize=100)
def geocode_city(city: str) -> tuple[float, float]:
    try:
        time.sleep(1.1)
        loc = GEOLOCATOR.geocode(city, exactly_one=True, timeout=10)
        if loc is None:
            raise ValueError(f"Could not geocode '{city}'")
        return loc.latitude, loc.longitude
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        raise ValueError(f"Geocoding error for '{city}': {e}") from e


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ========================= API SEARCHES =========================
def specific_flight_search(departure_iata: str, arrival_id: str, outbound_date: str):
    """SerpApi üzerinden en ucuz uçuşu bulur."""
    client = serpapi.Client(api_key=API_KEY)
    print(f"✈️  Flight search STARTING: {departure_iata} → {arrival_id} on {outbound_date}")
    try:
        results = client.search({
            "engine": "google_flights",
            "departure_id": departure_iata,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "currency": "EUR",
            "hl": "en",
            "gl": "it",
            "type": "2"
        })

        best_flights = results.get("best_flights", [])
        other_flights = results.get("other_flights", [])
        all_flights = best_flights + other_flights

        if not all_flights:
            print(f"   ❌ No flight found for {departure_iata}.")
            return None

        cheapest_flight = min(all_flights, key=lambda x: x.get("price", float('inf')))
        flight_info = cheapest_flight.get("flights", [{}])[0]
        price = cheapest_flight.get("price", 0)

        print(f"   ✅ Flight search DONE: cheapest price is {price}€")
        return {
            "airline": flight_info.get("airline", "Unknown"),
            "flight_duration": cheapest_flight.get("total_duration", 0),
            "flight_price": price,
            "link": results.get("search_metadata", {}).get("google_flights_url", "")
        }
    except Exception as e:
        print(f"⚠️  Flight API error ({departure_iata} → {arrival_id}): {e}")
        return None


def fetch_flixbus_city_id(query: str) -> str | None:
    """Canlı FlixBus API'sinden şehir ID'si çeker."""
    url = "https://global.api.flixbus.com/search/autocomplete/cities"
    params = {"q": query, "lang": "en", "flixbus_cities_only": "true"}
    try:
        resp = requests.get(url, params=params, headers=FLIXBUS_HEADERS, timeout=5)
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]["id"]
    except Exception:
        pass
    return None


def get_flixbus_id_from_db(city_name: str) -> str | None:
    """Önce veritabanına bakar, bulamazsa canlı API'ye sorar."""
    db_path = Path(__file__).parent.parent / "data" / "flixbus_europe.db"

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            query_lower = city_name.lower()
            cur.execute("""
                        SELECT id
                        FROM cities
                        WHERE LOWER(name) = ?
                           OR LOWER(search_terms) LIKE ?
                        """, (query_lower, f"%{query_lower}%"))
            row = cur.fetchone()
            conn.close()
            if row: return row[0]
        except Exception as e:
            print(f"   ⚠️ DB Error ({city_name}): {e}")

    return fetch_flixbus_city_id(city_name)


def flixbus_transfer(departure_location: str, arrival_location: str, departure_date: str):
    """FlixBus API'sini kullanarak otobüsleri çeker ve hataları yakalar."""
    dep_city = departure_location.split(",")[0].strip()
    arr_city = arrival_location.split(",")[0].strip()

    print(f"🚌 FlixBus search STARTING: {dep_city} → {arr_city} on {departure_date}")

    from_id = get_flixbus_id_from_db(dep_city)
    to_id = get_flixbus_id_from_db(arr_city)

    if not from_id or not to_id:
        print(f"   ❌ FlixBus: Cities could not be matched in API/DB.")
        return pd.DataFrame()

    f_date = datetime.strptime(departure_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    url = "https://global.api.flixbus.com/search/service/v4/search"
    params = {
        "from_city_id": from_id, "to_city_id": to_id,
        "departure_date": f_date, "products": '{"adult":1}',
        "currency": "EUR", "locale": "en_US", "search_by": "cities"
    }

    try:
        resp = requests.get(url, params=params, headers=FLIXBUS_HEADERS, timeout=10)

        if resp.status_code != 200:
            print(f"   ⚠️ FlixBus API Error: Status {resp.status_code} (Same city or invalid route).")
            return pd.DataFrame()

        data = resp.json()
        rows = []
        for trip in data.get("trips", []):
            for detail in trip.get("results", {}).values():
                price = detail.get("price", {}).get("total")
                if price is not None:
                    dur = detail.get("duration", {})
                    dur_min = int(dur.get("hours", 0)) * 60 + int(dur.get("minutes", 0))
                    rows.append({
                        "transport_mode": "bus",
                        "price": float(price),
                        "duration_min": dur_min,
                        "departure_dt": detail.get("departure", {}).get("date"),
                        "arrival_dt": detail.get("arrival", {}).get("date"),
                        "link": f"https://shop.global.flixbus.com/search?departureCity={from_id}&arrivalCity={to_id}&rideDate={f_date}&adult=1"
                    })

        df = pd.DataFrame(rows)
        if not df.empty:
            min_price = df["price"].min()
            print(f"   ✅ FlixBus DONE. Found {len(df)} bus(es). Cheapest: {min_price:.2f}€")
        else:
            print(f"   ✅ FlixBus DONE. Found 0 bus(es).")
        return df
    except Exception as e:
        print(f"❌ FlixBus ERROR: {e}")
        return pd.DataFrame()


def checkmybus_transfer(departure_location: str, arrival_location: str, departure_date: str,
                        include_buses: bool = False):
    """CheckMyBus API (Akıllı Filtreleme ile trenleri ve gerekiyorsa otobüsleri yakalar)"""
    client = CheckMyBusClient()
    mode_str = "Train & Bus" if include_buses else "Train"
    print(f"🚂 CheckMyBus ({mode_str}) search STARTING: {departure_location} → {arrival_location}")

    try:
        search_result = client.search(CheckMyBusSearchParams(
            departure_location=departure_location,
            arrival_location=arrival_location,
            departure_date=departure_date,
        ))
        df = search_result.to_dataframe()

        if "transport_mode" in df.columns:
            # Akıllı Filtre: Eğer include_buses False ise (yani FlixBus çalıştıysa), sadece trenleri tut.
            # Eğer include_buses True ise (FlixBus hata verdiyse), hem trenleri hem otobüsleri tut.
            if not include_buses:
                df = df[df["transport_mode"] == "train"].copy()
        else:
            df = pd.DataFrame()

        if not df.empty:
            min_price = df["price"].min()
            train_cnt = len(df[df["transport_mode"] == "train"]) if "transport_mode" in df.columns else 0
            bus_cnt = len(df[df["transport_mode"] == "bus"]) if "transport_mode" in df.columns else 0
            print(f"   ✅ CheckMyBus DONE. Found {train_cnt} train(s), {bus_cnt} bus(es). Cheapest: {min_price:.2f}€")
        else:
            print(f"   ✅ CheckMyBus DONE. Found 0 results.")

        return df
    except Exception as e:
        print(f"❌ CheckMyBus ERROR: {e}")
        return pd.DataFrame()


# ========================= MAIN ALGORITHM =========================
def a_to_b_via_ground_then_flight(departure_city: str,
                                  target_id: str,
                                  outbound_date: str,
                                  ground_date: str = None,
                                  max_distance_km: int = 300):
    if ground_date is None:
        ground_date = outbound_date

    print(f"\n🚀 REVERSE SEARCH STARTING: {departure_city} (Ground) -> Nearby Airports (Flight) -> {target_id}\n")

    try:
        lat, lon = geocode_city(departure_city)
    except ValueError as e:
        print(f"❌ {e}")
        return pd.DataFrame()

    nearby_airports = []
    for _, row in airports_df.iterrows():
        distance = haversine_km(lat, lon, row["lat"], row["lon"])
        if distance <= max_distance_km:
            bus_arrival_name = f"{row['city']}, {COUNTRY_EN.get(row['country'], row['country'])}"
            nearby_airports.append({
                "city": row["city"],
                "iata": row["iata"],
                "distance_km": round(distance, 1),
                "bus_arrival_name": bus_arrival_name
            })

    # UÇUŞ BULMA SINIRI KALDIRILDI! Sadece mesafeye göre sıralanacak.
    nearby_airports = sorted(nearby_airports, key=lambda x: x["distance_km"])

    if not nearby_airports:
        print(f"⚠️ No airports found within {max_distance_km}km limit.")
        return pd.DataFrame()

    print(f"Hey good news! Found {len(nearby_airports)} nearby airports. Scanning flights first...\n")

    combined = []
    for ap in nearby_airports:

        # 1. UÇUŞ ARA
        flight_data = specific_flight_search(ap['iata'], target_id, outbound_date)

        if not flight_data:
            print(f"   ⏭️  Skipping ground search for {ap['city']} ({ap['iata']}) (no flight).\n")
            continue

        # 2. FLIXBUS ARA (Otobüs)
        flixbus_df = flixbus_transfer(departure_city, ap["bus_arrival_name"], ground_date)

        # 3. CHECKMYBUS ARA (Akıllı Yedek Plan)
        # Eğer FlixBus API hatası verdiyse veya otobüs bulamadıysa (boş döndüyse), CheckMyBus'ta otobüsleri de ara.
        include_cmb_buses = flixbus_df.empty
        cmb_df = checkmybus_transfer(departure_city, ap["bus_arrival_name"], ground_date,
                                     include_buses=include_cmb_buses)

        print("-" * 50)

        frames_to_concat = []
        if not flixbus_df.empty: frames_to_concat.append(flixbus_df)
        if not cmb_df.empty: frames_to_concat.append(cmb_df)

        if not frames_to_concat:
            continue

        ground_df = pd.concat(frames_to_concat, ignore_index=True)

        for mode in ["bus", "train"]:
            mode_df = ground_df[ground_df.get("transport_mode", pd.Series(["bus"] * len(ground_df))) == mode]

            if not mode_df.empty:
                best_ground = mode_df.loc[mode_df["price"].idxmin()]

                combined.append({
                    "from_city": departure_city,
                    "transfer_city": ap["city"],
                    "transfer_iata": ap["iata"],
                    "ground_type": mode.capitalize(),
                    "ground_price": best_ground["price"],
                    "flight_price": flight_data["flight_price"],
                    "total_price": best_ground["price"] + flight_data["flight_price"],
                    "flight_airline": flight_data["airline"],
                    "distance_to_airport_km": ap["distance_km"],
                })

    if combined:
        result_df = pd.DataFrame(combined).sort_values("total_price").reset_index(drop=True)
        print(f"\n🎉 Process complete! Found {len(result_df)} alternative routes.")
        return result_df
    else:
        print("\n⚠️ No suitable Bus/Train + Flight combination found.")
        return pd.DataFrame()


# ========================= TEST EXECUTION =========================
if __name__ == "__main__":
    test_df = a_to_b_via_ground_then_flight(
        departure_city="Venice, Italy",
        target_id="NUE",
        outbound_date="2026-05-10",
        max_distance_km=250
    )

    if not test_df.empty:
        print("\n🏆 TOP 3 CHEAPEST COMBINATIONS:")
        print(test_df[["transfer_city", "transfer_iata", "ground_type", "ground_price", "flight_price",
                       "total_price"]].head(3))