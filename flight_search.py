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
            # Safely handle possible multi-leg flights
            leg = flight_data["flights"][0] if flight_data.get("flights") else {}
            return {
                "flight_type": flight_type,
                "airline": leg.get("airline", "Unknown"),
                "price": flight_data.get("price"),
                "departure_time": leg.get("departure_airport", {}).get("time"),
                "departure_iata": leg.get("departure_airport", {}).get("id"),
                "arrival_time": leg.get("arrival_airport", {}).get("time"),
                "arrival_iata": leg.get("arrival_airport", {}).get("id"),
                "duration": flight_data.get("total_duration"),
                "stops": flight_data.get("stops", 0),                    # new
                "flight_number": leg.get("flight_number", ""),           # new
                "link": flight_data.get("link"),                         # booking link if present
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
        else:
            print(f"✅ Flight search DONE: {len(df)} flights found "
                  f"({len(df[df['flight_type']=='Best'])} best)")

        return df.sort_values("price").reset_index(drop=True)

    except Exception as e:
        print(f"❌ ERROR in flight_search {departure_id} → {arrival_id}: {e}")
        return pd.DataFrame()   # return empty so your main pipeline never crashes