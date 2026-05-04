"""
CheckMyBus Python Client
========================
Reverse-engineered client for checkmybus.com - same interface pattern as
the Omio client in main.py.

Usage:
    from checkmybus import CheckMyBusClient, CheckMyBusSearchParams

    client = CheckMyBusClient()
    result = client.search(CheckMyBusSearchParams(
        departure_location="Frankfurt",
        arrival_location="Paris",
        departure_date="2026-04-16",
    ))

    df = result.to_dataframe()
    print(df.sort_values("price"))

Discovered: 2026-03-18 via JS bundle reverse engineering.
API: POST https://www.checkmybus.com/api/search (form-encoded, no auth)
"""

import re
import httpx
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel, Field

try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUTOCOMPLETE_URL = "https://autocomplete.checkmybus.com/api/complete/en-us/{query}?count=5"
_SEARCH_URL = "https://www.checkmybus.com/api/search"
_SEARCH_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Sec-Fetch": "true",
    "Accept": "application/json",
    "Referer": "https://www.checkmybus.com/",
    "Origin": "https://www.checkmybus.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# transportMedium int -> human-readable string
_TRANSPORT_MEDIUM = {
    0: "bus",
    1: "train",
    2: "car",
    3: "walking",
    4: "flight",
    5: "flight",
}

# .NET ticks epoch (100ns intervals since 0001-01-01)
_DOTNET_EPOCH = datetime(1, 1, 1)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CheckMyBusSearchParams(BaseModel):
    """Search parameters - mirrors OmioSearchParams from main.py."""
    departure_location: str = Field(..., description="Origin city name, e.g. 'Frankfurt'")
    arrival_location: str = Field(..., description="Destination city name, e.g. 'Paris'")
    departure_date: str = Field(..., description="YYYY-MM-DD format")
    adults: int = Field(default=1, ge=1, le=30)
    children: int = Field(default=0, ge=0)
    currency: str = Field(default="EUR")


class CheckMyBusTrip(BaseModel):
    """Single trip result - mirrors OmioSchedule from main.py."""
    trip_id: str = ""
    company: str = ""
    operator: str = ""
    company_logo_url: str = ""

    origin_city: str = ""
    origin_station: str = ""
    dest_city: str = ""
    dest_station: str = ""

    departure_dt: Optional[datetime] = None
    arrival_dt: Optional[datetime] = None
    duration_str: str = ""
    duration_min: Optional[int] = None

    price: Optional[float] = None
    currency: Optional[str] = None
    price_formatted: str = ""

    transport_mode: str = "bus"
    stops: int = 0

    sold_out: bool = False
    free_seats: int = 0
    seat_class: str = ""

    deep_link: Optional[str] = None
    checkmybus_search_url: str = ""

    @property
    def duration_display(self) -> str:
        if self.duration_str:
            return self.duration_str
        if not self.duration_min:
            return "N/A"
        h, m = divmod(self.duration_min, 60)
        return f"{h}h {m}m" if h else f"{m}m"


class CheckMyBusResult(BaseModel):
    """Full search response - mirrors OmioSearchResult from main.py."""
    trips: list[CheckMyBusTrip] = []
    total_count: int = 0
    query_id: str = ""
    departure_date: str = ""
    origin_name: str = ""
    destination_name: str = ""
    source: str = "checkmybus"

    def to_dataframe(self):
        """Return a flat Pandas DataFrame - one row per trip."""
        if not _PANDAS_AVAILABLE:
            raise ImportError("pandas is required: uv add pandas")
        rows = []
        for t in self.trips:
            rows.append({
                "company": t.company,
                "operator": t.operator,
                "origin_city": t.origin_city,
                "origin_station": t.origin_station,
                "dest_city": t.dest_city,
                "dest_station": t.dest_station,
                "departure_dt": t.departure_dt,
                "arrival_dt": t.arrival_dt,
                "duration_min": t.duration_min,
                "duration_str": t.duration_str,
                "price": t.price,
                "currency": t.currency,
                "price_formatted": t.price_formatted,
                "transport_mode": t.transport_mode,
                "stops": t.stops,
                "free_seats": t.free_seats,
                "seat_class": t.seat_class,
                "sold_out": t.sold_out,
                "deep_link": t.deep_link,
                "checkmybus_url": t.checkmybus_search_url,
                "source": "checkmybus",
            })
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_duration_min(duration_str: str) -> Optional[int]:
    """'8h 5m' -> 485, '45m' -> 45, '1h' -> 60, None if unparseable."""
    if not duration_str:
        return None
    total = 0
    h = re.search(r"(\d+)\s*h", duration_str)
    m = re.search(r"(\d+)\s*m", duration_str)
    if h:
        total += int(h.group(1)) * 60
    if m:
        total += int(m.group(1))
    return total if total > 0 else None


def _ticks_to_dt(ticks: int) -> Optional[datetime]:
    """.NET ticks (100ns since 0001-01-01) to Python datetime."""
    try:
        return _DOTNET_EPOCH + timedelta(microseconds=ticks // 10)
    except (OverflowError, OSError):
        return None


def _city_slug(name: str) -> str:
    """'Frankfurt am Main, Germany' -> 'frankfurt-am-main'"""
    # Take only city part (before comma)
    city = name.split(",")[0].strip()
    slug = city.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")


def _build_search_url(origin_name: str, dest_name: str) -> str:
    return (
        f"https://www.checkmybus.com"
        f"/{_city_slug(origin_name)}/{_city_slug(dest_name)}"
        f"?mode=search"
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class CheckMyBusClient:
    """
    Client for checkmybus.com - same interface as OmioChatGPTClient.

    Example:
        client = CheckMyBusClient()
        result = client.search(CheckMyBusSearchParams(
            departure_location="Frankfurt",
            arrival_location="Paris",
            departure_date="2026-04-16",
        ))
        df = result.to_dataframe()
    """

    def __init__(self, timeout: float = 30.0):
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
        )
        # In-memory city UUID cache: "Frankfurt" -> CityData dict
        self._city_cache: dict[str, dict] = {}

    def search(self, params: CheckMyBusSearchParams) -> CheckMyBusResult:
        """Fetch bus/train/rideshare results for the given route and date."""
        origin = self._resolve_city(params.departure_location)
        dest = self._resolve_city(params.arrival_location)

        raw = self._post_search(
            origin=origin,
            dest=dest,
            departure_date=params.departure_date,
            currency=params.currency,
            adults=params.adults,
            children=params.children,
        )

        search_url = _build_search_url(origin["Name"], dest["Name"])
        return self._parse_result(raw, params, search_url)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _resolve_city(self, query: str) -> dict:
        """Return city metadata dict from autocomplete (cached)."""
        key = query.strip().lower()
        if key in self._city_cache:
            return self._city_cache[key]

        resp = self._http.get(
            f"https://autocomplete.checkmybus.com/api/complete/en-us/{query}",
            params={"count": 5},
            headers={"User-Agent": _SEARCH_HEADERS["User-Agent"]},
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            raise ValueError(f"No city found for: {query!r}")

        city = results[0]
        self._city_cache[key] = city
        return city

    def _post_search(
        self,
        origin: dict,
        dest: dict,
        departure_date: str,
        currency: str,
        adults: int,
        children: int,
    ) -> dict:
        """POST to /api/search and return raw JSON dict."""
        data = {
            "originId": origin["Id"],
            "originParentId": origin.get("ParentId", "00000000-0000-0000-0000-000000000000"),
            "originIsCity": "true",
            "originIsAirport": "false",
            "destinationId": dest["Id"],
            "destinationParentId": dest.get("ParentId", "00000000-0000-0000-0000-000000000000"),
            "destinationIsCity": "true",
            "destinationIsAirport": "false",
            "latitudeFrom": str(origin["Latitude"]),
            "longitudeFrom": str(origin["Longitude"]),
            "nameFrom": origin["Name"],
            "latitudeTo": str(dest["Latitude"]),
            "longitudeTo": str(dest["Longitude"]),
            "nameTo": dest["Name"],
            "departureDate": departure_date,
            "currency": currency,
            "culture": "en-US",
            "searchRadius": "5",
            "adults": str(adults),
            "children": str(children),
            "clientToken": "",
        }
        resp = self._http.post(
            _SEARCH_URL,
            data=data,
            headers=_SEARCH_HEADERS,
        )
        resp.raise_for_status()
        return resp.json()

    def _parse_result(
        self,
        raw: dict,
        params: CheckMyBusSearchParams,
        search_url: str,
    ) -> CheckMyBusResult:
        trips: list[CheckMyBusTrip] = []

        for conn in raw.get("connectionResultModels", []):
            for item in conn.get("connectionResultItems", []):
                trip = self._parse_trip(item, search_url)
                trips.append(trip)

        return CheckMyBusResult(
            trips=trips,
            total_count=raw.get("numberOfConnections", len(trips)),
            query_id=raw.get("queryId", ""),
            departure_date=params.departure_date,
            origin_name=raw.get("neutralOriginCityName", params.departure_location),
            destination_name=raw.get("neutralDestinationCityName", params.arrival_location),
        )

    def _parse_trip(self, item: dict, search_url: str) -> CheckMyBusTrip:
        # Departure datetime
        dep_dt: Optional[datetime] = None
        dep_str = item.get("departureDateTime")
        if dep_str:
            try:
                dep_dt = datetime.fromisoformat(dep_str)
            except ValueError:
                pass

        # Arrival via .NET ticks
        arr_dt: Optional[datetime] = None
        arr_ticks = item.get("arrivalTicks")
        if arr_ticks:
            arr_dt = _ticks_to_dt(arr_ticks)

        # Price
        price_obj = item.get("price") or {}
        price_val = price_obj.get("value")
        price_currency = price_obj.get("currencyCode", "EUR")
        price_fmt = price_obj.get("formatted", "")

        # Duration
        dur_str = item.get("duration", "")
        dur_min = _parse_duration_min(dur_str)

        # Transport mode
        transport_int = item.get("transportMedium", 0)
        transport_mode = _TRANSPORT_MEDIUM.get(transport_int, "bus")

        # Seat info
        seat_info = item.get("seatInfo") or {}
        free_seats = seat_info.get("freeSeats", 0)
        seat_class = seat_info.get("seatClassName", "")

        # Booking link
        deep_link = item.get("deepLink")

        return CheckMyBusTrip(
            trip_id=item.get("tripId", ""),
            company=item.get("companyName", ""),
            operator=item.get("operatorName") or item.get("companyName", ""),
            company_logo_url=item.get("companyNameLogo", ""),
            origin_city=item.get("originCityName", ""),
            origin_station=item.get("originStationName", ""),
            dest_city=item.get("destinationCityName", ""),
            dest_station=item.get("destinationStationName", ""),
            departure_dt=dep_dt,
            arrival_dt=arr_dt,
            duration_str=dur_str,
            duration_min=dur_min,
            price=price_val,
            currency=price_currency,
            price_formatted=price_fmt,
            transport_mode=transport_mode,
            stops=item.get("stopover") or 0,
            sold_out=item.get("soldout", False),
            free_seats=free_seats,
            seat_class=seat_class,
            deep_link=deep_link,
            checkmybus_search_url=search_url,
        )

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
