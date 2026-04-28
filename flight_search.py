from serpapi import Client as SerpApiClient  # clearer import
import pandas as pd
import os

def flight_search(departure_id: str, arrival_id: str, outbound_date: str):
    """
    Search direct/specific flights from departure_id → arrival_id.
    Returns ONE DataFrame with both 'Best' and 'Other' flights.
    """
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        raise ValueError("SERPAPI_KEY environment variable not set!")

    print(f"✈️  Flight search STARTING: {departure_id} → {arrival_id} on {outbound_date}")

    try:
        client = SerpApiClient(api_key=api_key)
        results = client.search({
            "engine": "google_flights",
            "departure_id": departure_id.upper(),
            "arrival_id": arrival_id.upper(),
            "currency": "EUR",
            "type": "2",           # round-trip? change if you want one-way
            "outbound_date": outbound_date,
            "no_cache": False,
        })

        best_flights = results.get("best_flights", [])
        other_flights = results.get("other_flights", [])

        rows = []

        def extract_flight(flight_data, flight_type: str):
            flights = flight_data.get("flights", []) or []
            first = flights[0] if flights else {}
            last = flights[-1] if flights else {}
            legs = [
                {
                    "airline": f.get("airline", ""),
                    "flight_number": f.get("flight_number", ""),
                    "dep": f.get("departure_airport", {}).get("time", ""),
                    "arr": f.get("arrival_airport", {}).get("time", ""),
                    "from": f.get("departure_airport", {}).get("id", ""),
                    "to": f.get("arrival_airport", {}).get("id", ""),
                    "from_name": f.get("departure_airport", {}).get("name", ""),
                    "to_name": f.get("arrival_airport", {}).get("name", ""),
                    "duration": f.get("duration", 0),
                }
                for f in flights
            ]
            layovers = [
                {
                    "duration": lay.get("duration", 0),
                    "airport": lay.get("id", ""),
                    "name": lay.get("name", ""),
                }
                for lay in (flight_data.get("layovers") or [])
            ]
            stops = flight_data.get("stops")
            if stops is None:
                stops = max(0, len(flights) - 1)
            return {
                "flight_type": flight_type,
                "airline": first.get("airline", "Unknown"),
                "price": flight_data.get("price"),
                "departure_time": first.get("departure_airport", {}).get("time"),
                "departure_iata": first.get("departure_airport", {}).get("id"),
                "arrival_time": last.get("arrival_airport", {}).get("time"),
                "arrival_iata": last.get("arrival_airport", {}).get("id"),
                "duration": flight_data.get("total_duration"),
                "stops": stops,
                "flight_number": first.get("flight_number", ""),
                "link": flight_data.get("link"),
                "legs": legs,
                "layovers": layovers,
            }

        # Best flights
        for f in best_flights:
            rows.append(extract_flight(f, "Best"))

        # Other flights
        for f in other_flights:
            rows.append(extract_flight(f, "Other"))

        df = pd.DataFrame(rows)

        if df.empty:
            print(f"⚠️  No flights found from {departure_id} to {arrival_id}")
            return df

        print(f"✅ Flight search DONE: {len(df)} flights found "
              f"({len(df[df['flight_type']=='Best'])} best)")
        return df.sort_values("price").reset_index(drop=True)

    except Exception as e:
        print(f"❌ ERROR in flight_search {departure_id} → {arrival_id}: {e}")
        return pd.DataFrame()   # return empty so your main pipeline never crashes