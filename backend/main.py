from .flight_and_ground_search import find_cheap_flight_plus_ground
from .flight_search import flight_search
if __name__ == "__main__":
    df = find_cheap_flight_plus_ground(
        departure_id="VCE",
        target_city="Nuremberg",
        outbound_date="2026-05-10",
        ground_date="2026-05-10"
    )
    print(df)
    df = flight_search(departure_id="VCE", arrival_id="NUE", outbound_date="2026-05-10")
    print(df)