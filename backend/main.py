from .flight_plus_bus_search import find_cheap_flight_plus_ground_v2
from .flight_search import flight_search
if __name__ == "__main__":
    hubs = find_cheap_flight_plus_ground_v2(
        departure_id="MXP,LIN",
        target_city="Nuremberg",
        outbound_date="2026-05-10",
        ground_date="2026-05-10",
    )
    for h in hubs:
        print(
            f"{h['hub']['iata']:>4}  {h['hub']['city']:<14}"
            f"  flights={len(h['flight_options']):>2}"
            f"  buses={len(h['bus_options']):>2}"
            f"  min_total={h['min_total_price']}"
        )
    df = flight_search(departure_id="MXP,LIN", arrival_id="NUE", outbound_date="2026-05-10")
    print(df)
