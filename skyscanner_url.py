"""
Build Skyscanner search URLs pre-filtered with Google Flights data.

Filters applied:
  departure-times  → ±WINDOW_MIN minute window around the actual departure
  stops            → exact stop count matched to Skyscanner's filter values
"""

SKYSCANNER_BASE = "https://www.skyscanner.com.tr/tasima/ucak-bileti"
_FIXED_PARAMS = (
    "adultsv2=1&cabinclass=economy&childrenv2=&ref=home"
    "&rtn=0&outboundaltsenabled=false&inboundaltsenabled=false"
)
WINDOW_MIN = 30  # ±minutes around departure time


def _date_to_yymmdd(date_str: str) -> str:
    d = date_str[:10]
    return d[2:].replace("-", "")


def _time_to_minutes(dt_str: str) -> int:
    """'2026-05-10 06:00' or '06:00' → 360"""
    s = str(dt_str).strip()
    if " " in s:
        s = s.split(" ", 1)[1]
    h, m = s[:5].split(":")
    return int(h) * 60 + int(m)


def _stops_param(stops: int) -> str:
    if stops == 0:
        return "stops=!oneStop,!twoPlusStops"
    if stops == 1:
        return "stops=!direct,!twoPlusStops"
    return "stops=!direct,!oneStop"


def build_skyscanner_url(
    dep_iata: str,
    arr_iata: str,
    dep_datetime: str,
    stops: int,
    total_duration_min: int | None = None,
    currency: str = "EUR",
) -> str:
    """
    Return a Skyscanner filtered search URL derived from Google Flights data.

    dep_datetime:       '2026-05-10 06:00' (SerpAPI departure_airport.time format)
    stops:              number of stops (0 = direct, 1 = one stop, etc.)
    total_duration_min: SerpAPI total_duration in minutes; becomes Skyscanner's
                        max-duration filter so only flights at most this long appear.
    """
    dep = dep_iata.lower()
    arr = arr_iata.lower()
    date_c = _date_to_yymmdd(dep_datetime)

    dep_min = _time_to_minutes(dep_datetime)
    start = max(0, dep_min - WINDOW_MIN)
    end = min(1440, dep_min + WINDOW_MIN)

    params = f"{_FIXED_PARAMS}&currency={currency}"
    if total_duration_min is not None:
        params += f"&duration={int(total_duration_min)}"
    params += f"&departure-times={start}-{end}&{_stops_param(stops)}"

    return f"{SKYSCANNER_BASE}/{dep}/{arr}/{date_c}/?{params}"
