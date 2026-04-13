import json
import math
import os
import unicodedata
from datetime import date, datetime, timedelta

import pandas as pd
import questionary
from rapidfuzz import fuzz
import serpapi

from checkmybus import CheckMyBusClient, CheckMyBusSearchParams

API_KEY = os.environ.get("SERPAPI_KEY", "")
NEARBY_RADIUS_KM = 300
BUS_BUFFER_HOURS = 2  # uçuş varışından sonra otobüs için minimum bekleme
_COUNTRY_CODE_MAP = {"DE": "Germany", "IT": "Italy"}


# ---------------------------------------------------------------------------
# Airport helpers
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def load_airports(path: str = "airports.json", countries: list[str] | None = None) -> pd.DataFrame:
    """
    countries=["IT","DE"]  → TUI seçimi için (sadece IT/DE havalimanları)
    countries=None          → ara havalimanı araması için (dünya geneli)
    """
    with open(path) as f:
        raw = json.load(f)
    df = pd.DataFrame.from_dict(raw, orient="index")
    if countries:
        df = df[df["country"].isin(countries)]
    df = df[df["iata"] != ""]
    return df.reset_index(drop=True)


def search(df: pd.DataFrame, query: str, limit: int = 5) -> list[dict]:
    norm_query = normalize(query)
    city_scores = df["city"].map(normalize).map(lambda c: fuzz.partial_ratio(norm_query, c))
    name_scores = df["name"].map(normalize).map(lambda n: fuzz.WRatio(norm_query, n))
    best_scores = pd.concat([city_scores, name_scores], axis=1).max(axis=1)
    top_idx = best_scores.nlargest(limit).index
    return [
        {
            "name": df.loc[i, "name"],
            "iata": df.loc[i, "iata"],
            "icao": df.loc[i, "icao"],
            "city": df.loc[i, "city"],
            "country": df.loc[i, "country"],
            "lat": df.loc[i, "lat"],
            "lon": df.loc[i, "lon"],
        }
        for i in top_idx
    ]


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearby_airports(df: pd.DataFrame, lat: float, lon: float, radius_km: float) -> list[str]:
    return [
        row["iata"]
        for _, row in df.iterrows()
        if haversine_km(lat, lon, row["lat"], row["lon"]) <= radius_km
    ]


def nearest_airport_iata(df: pd.DataFrame, lat: float, lon: float) -> str | None:
    """Verilen koordinata en yakın havalimanının IATA kodunu döndürür."""
    best_iata, best_dist = None, float("inf")
    for _, row in df.iterrows():
        d = haversine_km(lat, lon, row["lat"], row["lon"])
        if d < best_dist:
            best_dist, best_iata = d, row["iata"]
    return best_iata


# ---------------------------------------------------------------------------
# TUI helpers
# ---------------------------------------------------------------------------

_RETRY = object()


def ask_airport(df: pd.DataFrame, prompt: str) -> dict | None:
    while True:
        query = questionary.text(prompt).ask()
        if not query:
            return None
        matches = search(df, query)
        if not matches:
            print("Eşleşme bulunamadı.")
            continue
        choices = [
            questionary.Choice(
                title=f"{a['name']}  [{a['iata']} / {a['icao']}]  {a['city']}, {a['country']}",
                value=a,
            )
            for a in matches
        ]
        choices.append(questionary.Choice(title="-- Yeniden ara --", value=_RETRY))
        selected = questionary.select("Havalimanı seçin:", choices=choices).ask()
        if selected is _RETRY:
            continue
        return selected


def ask_date() -> str | None:
    while True:
        raw = questionary.text("Uçuş tarihi (YYYY-MM-DD):").ask()
        if not raw:
            return None
        try:
            d = datetime.strptime(raw.strip(), "%Y-%m-%d").date()
            if d < date.today():
                print("Geçmiş bir tarih girdiniz, tekrar deneyin.")
                continue
            return str(d)
        except ValueError:
            print("Format hatalı, örnek: 2026-05-10")


# ---------------------------------------------------------------------------
# Flight helpers
# ---------------------------------------------------------------------------

def fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}s {m}dk" if h else f"{m}dk"


def _parse_item(item: dict) -> dict:
    legs = item["flights"]
    first, last = legs[0], legs[-1]
    airlines = list(dict.fromkeys(l["airline"] for l in legs))
    flight_nos = [l["flight_number"] for l in legs]

    layover_info = []
    for i, lay in enumerate(item.get("layovers", [])):
        layover_info.append({
            "airport": lay.get("name", ""),
            "duration_min": lay.get("duration", 0),
        })

    return {
        "airline": " / ".join(airlines),
        "flight_nos": ", ".join(flight_nos),
        "departure_time": first["departure_airport"]["time"],
        "arrival_time": last["arrival_airport"]["time"],
        "departure_iata": first["departure_airport"]["id"],
        "arrival_iata": last["arrival_airport"]["id"],
        "arrival_airport_name": last["arrival_airport"]["name"],
        "duration_min": item["total_duration"],
        "price_eur": item["price"],
        "stops": len(legs) - 1,
        "layovers": layover_info,
    }


def pareto_filter(flights: list[dict], max_results: int = 4) -> list[dict]:
    """
    Fiyat ve süre üzerinden Pareto-optimal uçuşları döndürür.
    Bir uçuş hem daha ucuz hem daha kısa başka bir uçuş varsa elenir.
    Kalan uçuşlar fiyata göre sıralanır ve max_results ile kırpılır.
    """
    pareto = []
    for f in flights:
        dominated = any(
            o["price_eur"] <= f["price_eur"]
            and o["duration_min"] <= f["duration_min"]
            and (o["price_eur"] < f["price_eur"] or o["duration_min"] < f["duration_min"])
            for o in flights
            if o is not f
        )
        if not dominated:
            pareto.append(f)
    return sorted(pareto, key=lambda f: f["price_eur"])[:max_results]


def fetch_flights(dep_iata: str, arr_iata: str, outbound_date: str) -> dict:
    """Origin → destination direkt arama. Yakın havalimanı yok, tek call."""
    client = serpapi.Client(api_key=API_KEY)
    raw = client.search({
        "engine": "google_flights",
        "departure_id": dep_iata,
        "arrival_id": arr_iata,
        "outbound_date": outbound_date,
        "currency": "EUR",
        "type": "2",
        "no_cache": False,
    })
    best = [_parse_item(i) for i in raw.get("best_flights", [])]
    other_raw = [_parse_item(i) for i in raw.get("other_flights", [])]
    other = pareto_filter(other_raw, max_results=4)
    return {"best": best, "other": other}


def fetch_explore(dep_iata: str, dest_lat: float, dest_lon: float, radius_km: float) -> list[dict]:
    """
    Google Travel Explore: origin'den tüm destination'lara en ucuz uçuşlar.
    Dönen sonuçları destination'a yakınlığa göre filtreler.
    Option 3 (uçuş + otobüs) için kullanılır.
    """
    client = serpapi.Client(api_key=API_KEY)
    raw = client.search({
        "engine": "google_travel_explore",
        "departure_id": dep_iata,
        "currency": "EUR",
        "type": "2",
        "no_cache": False,
    })
    nearby = []
    for d in raw.get("destinations", []):
        if "flight_price" not in d or "gps_coordinates" not in d:
            continue
        lat = d["gps_coordinates"]["latitude"]
        lon = d["gps_coordinates"]["longitude"]
        dist = haversine_km(lat, lon, dest_lat, dest_lon)
        if dist <= radius_km:
            nearby.append({
                "city": d["name"],
                "country": d.get("country", ""),
                "lat": lat,
                "lon": lon,
                "price_eur": d["flight_price"],
                "duration_min": d.get("flight_duration", 0),
                "airline": d.get("airline", ""),
                "dist_km": round(dist),
            })
    return sorted(nearby, key=lambda d: d["price_eur"])


# ---------------------------------------------------------------------------
# Bus helpers
# ---------------------------------------------------------------------------

def _fmt_trip_line(trip) -> str:
    dep = trip.departure_dt.strftime("%H:%M") if trip.departure_dt else "?"
    arr = trip.arrival_dt.strftime("%H:%M") if trip.arrival_dt else "?"
    mode = "tren" if trip.transport_mode == "train" else "otobüs"
    return f"{trip.price:.2f} EUR  {dep}→{arr}  {trip.duration_display}  {trip.company} ({mode})"


def fetch_transport_options(
    from_city: str,
    to_city: str,
    travel_date: str,
    after_dt: datetime | None = None,
) -> dict | None:
    """
    from_city → to_city için tüm ulaşım seçeneklerini çeker.
    after_dt verilmişse yalnızca o saatten sonra kalkan seferler değerlendirilir.
    Döner: {cheapest_bus, earliest_bus, cheapest_train, earliest_train}
    Hiçbir uygun sefer yoksa None döner.
    """
    label = f"  {from_city} → {to_city}"
    after_str = f" (uçuş varışı+{BUS_BUFFER_HOURS}s sonrası: {after_dt.strftime('%H:%M')})" if after_dt else ""
    try:
        with CheckMyBusClient() as client:
            result = client.search(CheckMyBusSearchParams(
                departure_location=from_city,
                arrival_location=to_city,
                departure_date=travel_date,
            ))
        trips = [t for t in result.trips if not t.sold_out and t.price is not None]
        if not trips:
            print(f"{label}: sefer bulunamadı (satışa açılmamış veya tümü dolu)")
            return None

        if after_dt:
            trips = [t for t in trips if t.departure_dt and t.departure_dt >= after_dt]
        if not trips:
            print(f"{label}: uçuş sonrasına{after_str} uygun sefer yok")
            return None

        buses = [t for t in trips if t.transport_mode != "train"]
        trains = [t for t in trips if t.transport_mode == "train"]

        cheapest_bus = min(buses, key=lambda t: t.price) if buses else None
        earliest_bus = (
            min((t for t in buses if t.departure_dt), key=lambda t: t.departure_dt)
            if buses else None
        )
        if earliest_bus is cheapest_bus:
            earliest_bus = None

        cheapest_train = min(trains, key=lambda t: t.price) if trains else None
        earliest_train = (
            min((t for t in trains if t.departure_dt), key=lambda t: t.departure_dt)
            if trains else None
        )
        if earliest_train is cheapest_train:
            earliest_train = None

        if cheapest_bus:
            print(f"{label}{after_str}: en ucuz otobüs  -> {_fmt_trip_line(cheapest_bus)}")
        if earliest_bus:
            print(f"  {'':>{len(from_city)}}   en erken otobüs -> {_fmt_trip_line(earliest_bus)}")
        if cheapest_train:
            print(f"  {'':>{len(from_city)}}   en ucuz tren    -> {_fmt_trip_line(cheapest_train)}")
        if earliest_train:
            print(f"  {'':>{len(from_city)}}   en erken tren   -> {_fmt_trip_line(earliest_train)}")

        if not cheapest_bus and not cheapest_train:
            return None

        return {
            "cheapest_bus": cheapest_bus,
            "earliest_bus": earliest_bus,
            "cheapest_train": cheapest_train,
            "earliest_train": earliest_train,
        }
    except Exception as e:
        print(f"{label}: bağlantı hatası ({type(e).__name__}: {e})")
        return None


# ---------------------------------------------------------------------------
# Option building
# ---------------------------------------------------------------------------

def build_options(
    flights: dict,
    explore: list[dict],
    dest_city: str,
    dest_country: str,
    travel_date: str,
    df: pd.DataFrame,
    origin_iata: str,
) -> tuple:
    all_flights = flights["best"] + flights["other"]

    # Seçenek 1: Google'ın önerdiği en iyi uçuş
    opt1 = flights["best"][0] if flights["best"] else None

    # Seçenek 2: Tüm uçuşlar arasından en ucuz (Pareto filtreli havuzdan)
    opt2 = min(all_flights, key=lambda f: f["price_eur"]) if all_flights else None

    # Seçenek 3: Explore'dan yakın şehir + otobüs
    opt1_price = opt1["price_eur"] if opt1 else float("inf")
    candidates = [
        d for d in explore
        if d["price_eur"] < opt1_price and d["city"].lower() != dest_city.lower()
    ][:3]

    opt3 = None
    if not candidates:
        print(f"  Yakın alternatif bulunamadı (mevcut en iyi uçuş {opt1_price}€ altında seçenek yok).")
    else:
        print(f"\n  Otobüs kombinasyonu için {len(candidates)} ara şehir deneniyor: {', '.join(d['city'] for d in candidates)}")

        dest_country_name = _COUNTRY_CODE_MAP.get(dest_country, dest_country)
        dest_location = f"{dest_city}, {dest_country_name}"

        def search_combo(dest: dict) -> dict | None:
            # 1. Ara havalimanını bul
            intermediate_iata = nearest_airport_iata(df, dest["lat"], dest["lon"])
            if not intermediate_iata:
                return None

            # 2. Gerçek uçuşu çek -- varış saatini bilmek için şart
            detail = fetch_flights(origin_iata, intermediate_iata, travel_date)
            detail_all = detail["best"] + detail["other"]
            if not detail_all:
                print(f"  {dest['city']}: {intermediate_iata} için uçuş bulunamadı, atlanıyor.")
                return None
            best_flight = min(detail_all, key=lambda f: f["price_eur"])

            # 3. Uçuş varışından BUS_BUFFER_HOURS sonrasını after_dt olarak ayarla
            after_dt = None
            try:
                arr_dt = datetime.strptime(best_flight["arrival_time"], "%Y-%m-%d %H:%M")
                after_dt = arr_dt + timedelta(hours=BUS_BUFFER_HOURS)
            except (ValueError, KeyError):
                pass

            # 4. Otobüs/tren seçeneklerini çek (after_dt filtreli)
            from_location = f"{dest['city']}, {dest['country']}"
            transport = fetch_transport_options(from_location, dest_location, travel_date, after_dt)
            if not transport or not transport["cheapest_bus"]:
                return None

            total = best_flight["price_eur"] + transport["cheapest_bus"].price
            return {
                "explore": dest,
                "transport": transport,
                "flight_detail": best_flight,
                "intermediate_iata": intermediate_iata,
                "total": total,
            }

        combos = [r for d in candidates if (r := search_combo(d)) is not None]

        if not combos:
            print("  Hiçbir ara şehirden uygun kombinasyon bulunamadı.")
        else:
            best_combo = min(combos, key=lambda c: c["total"])
            exp = best_combo["explore"]
            print(f"  En iyi kombinasyon: {exp['city']} uçuşu + kara ulaşımı")
            opt3 = best_combo

    return opt1, opt2, opt3


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

SEP = "=" * 62


def _print_flight(flight: dict, label: str = "Uçuş") -> None:
    stops_str = "Direkt" if flight["stops"] == 0 else f"{flight['stops']} aktarma"
    print(f"  [{label}]")
    print(f"  Havayolu  : {flight['airline']}")
    print(f"  Sefer     : {flight['flight_nos']}")
    print(f"  Kalkış    : {flight['departure_time']}  ({flight['departure_iata']})")
    print(f"  Varış     : {flight['arrival_time']}  ({flight['arrival_iata']}) - {flight['arrival_airport_name']}")
    print(f"  Süre      : {fmt_duration(flight['duration_min'])}  |  {stops_str}")
    if flight["layovers"]:
        for lay in flight["layovers"]:
            print(f"  Aktarma   : {lay['airport']}  {fmt_duration(lay['duration_min'])}")
    print(f"  Fiyat     : {flight['price_eur']} EUR")


def display_options(opt1, opt2, opt3, dest_iata: str) -> None:
    print(f"\n{SEP}")
    print("SEÇENEK 1  |  En iyi direkt uçuş  (Google önerisi)")
    print(SEP)
    if opt1:
        _print_flight(opt1)
    else:
        print("  Bu güzergahta direkt uçuş bulunamadı.")

    print(f"\n{SEP}")
    print("SEÇENEK 2  |  En ucuz uçuş")
    print(SEP)
    if opt2:
        _print_flight(opt2)
        if opt2.get("arrival_iata") != dest_iata:
            print(f"  ** Hedef havalimanı değil -- yakın havalimanı, ek ulaşım gerekebilir.")
    else:
        print("  Uçuş bulunamadı.")

    print(f"\n{SEP}")
    print("SEÇENEK 3  |  Uçak + Kara Ulaşımı  (toplam en ucuz)")
    print(SEP)
    if opt3:
        exp = opt3["explore"]
        transport = opt3["transport"]
        fd = opt3["flight_detail"]

        _print_flight(fd, label="Uçuş")

        def _print_land(trip, label: str) -> None:
            mode = "Tren" if trip.transport_mode == "train" else "Otobüs"
            print(f"\n  [{label} - {mode}]  {exp['city']} → {trip.dest_city}  ({exp['dist_km']}km)")
            print(f"  Şirket    : {trip.company}")
            print(f"  Güzergah  : {trip.origin_city} ({trip.origin_station}) -> {trip.dest_city} ({trip.dest_station})")
            if trip.departure_dt:
                print(f"  Kalkış    : {trip.departure_dt.strftime('%H:%M')}")
            if trip.arrival_dt:
                print(f"  Varış     : {trip.arrival_dt.strftime('%H:%M')}")
            print(f"  Süre      : {trip.duration_display}")
            print(f"  Fiyat     : {trip.price:.2f} EUR")
            print(f"  TOPLAM    : {fd['price_eur']} + {trip.price:.2f} = {fd['price_eur'] + trip.price:.2f} EUR")

        _print_land(transport["cheapest_bus"], "En Ucuz Otobüs")
        if transport["earliest_bus"]:
            _print_land(transport["earliest_bus"], "En Erken Otobüs")
        if transport["cheapest_train"]:
            _print_land(transport["cheapest_train"], "En Ucuz Tren")
        if transport["earliest_train"]:
            _print_land(transport["earliest_train"], "En Erken Tren")
    else:
        print("  Yakın havalimanı + kara ulaşımı kombinasyonu bulunamadı.")

    print(f"\n{SEP}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    df = load_airports(countries=["IT", "DE"])   # TUI seçimi için
    df_all = load_airports()                      # ara havalimanı araması için (dünya geneli)

    origin = ask_airport(df, "Kalkmak istediğiniz yeri girin:")
    if not origin:
        return

    destination = ask_airport(df, "Varmak istediğiniz yeri girin:")
    if not destination:
        return

    outbound_date = ask_date()
    if not outbound_date:
        return

    print(f"\nKalkış  : {origin['name']} ({origin['iata']})")
    print(f"Varış   : {destination['name']} ({destination['iata']})")
    print(f"Tarih   : {outbound_date}")

    print("\nUçuşlar aranıyor...")
    flights = fetch_flights(origin["iata"], destination["iata"], outbound_date)

    if not flights["best"] and not flights["other"]:
        print("Bu güzergah ve tarih için uçuş bulunamadı.")
        return

    print("Yakın alternatifler ve otobüs kontrol ediliyor...")
    explore = fetch_explore(origin["iata"], destination["lat"], destination["lon"], NEARBY_RADIUS_KM)

    opt1, opt2, opt3 = build_options(
        flights=flights,
        explore=explore,
        dest_city=destination["city"],
        dest_country=destination["country"],
        travel_date=outbound_date,
        df=df_all,
        origin_iata=origin["iata"],
    )

    display_options(opt1, opt2, opt3, destination["iata"])


if __name__ == "__main__":
    main()
