"""Tests for backend.bus_flight_bus_search pure functions.

Covers _default_trio (selection rule) and _min_valid_total_three_legs
(sort/summary). The fetch + explore paths are skipped here — they hit
SerpAPI / FlixBus and are exercised by the live smoke test instead.
"""
from __future__ import annotations

from backend.bus_flight_bus_search import (
    _COMFORT_HOURS,
    _default_trio,
    _has_comfortable_bus_after_flight,
    _has_comfortable_bus_before_flight,
    _min_valid_total_three_legs,
)

DATE = "2026-05-10"


def _bus(idx: int, dep: str, arr: str, price: float | None,
         date: str = DATE, arr_date: str | None = None) -> dict:
    return {
        "id": f"b{idx}",
        "price_eur": price,
        "duration_min": 120,
        "departure_dt": f"{date}T{dep}:00",
        "arrival_dt": f"{arr_date or date}T{arr}:00",
        "origin": "x",
        "destination": "y",
        "url": None,
    }


def _flight(idx: int, dep: str, arr: str, price: float | None) -> dict:
    return {
        "id": f"f{idx}",
        "type": "Best",
        "airline": "AA",
        "flight_number": "1",
        "price_eur": price,
        "duration_min": 90,
        "stops": 0,
        "departure_time": f"{DATE} {dep}",
        "arrival_time": f"{DATE} {arr}",
        "departure_iata": "MUC",
        "arrival_iata": "ZRH",
        "legs": [],
        "layovers": [],
        "link": None,
    }


# ── _default_trio ────────────────────────────────────────────────────────────

def test_default_trio_picks_cheapest_flight_with_valid_buses():
    # Flight 0 dep 10:00 arr 11:30 — bus1 must arrive ≤ 08:00, bus2 must depart ≥ 13:30.
    bus1 = [
        _bus(0, "06:00", "08:00", 30.0),  # arr 08:00 — valid, exactly on deadline
        _bus(1, "07:00", "09:00", 25.0),  # arr 09:00 — invalid (> 08:00)
    ]
    flights = [
        _flight(0, "10:00", "11:30", 60.0),  # cheapest
        _flight(1, "12:00", "13:30", 80.0),
    ]
    bus2 = [
        _bus(0, "13:30", "16:00", 20.0),  # dep 13:30 — valid, earliest
        _bus(1, "13:31", "16:00", 25.0),
        _bus(2, "15:00", "17:00", 22.0),
    ]
    trio = _default_trio(bus1, flights, bus2, DATE, 2.0)
    assert trio == {"bus1_idx": 0, "bus1_source": "same",
                    "flight_idx": 0,
                    "bus2_idx": 0, "bus2_source": "same"}


def test_default_trio_prefers_cheapest_flight_over_cheaper_total():
    # New rule: cheapest flight wins as long as it has valid buses, even if
    # a pricier flight would yield a cheaper end-to-end total. The user
    # explicitly asked for "cheapest flight + closest-time buses" instead of
    # "cheapest total" because total-price defaults felt unintuitive.
    bus1 = [
        _bus(0, "07:00", "09:00", 100.0),  # valid only for f0 (≤ 10:00)
        _bus(1, "08:00", "11:30", 5.0),    # valid only for f1 (≤ 12:00)
    ]
    flights = [
        _flight(0, "12:00", "13:30", 50.0),  # cheapest — wins
        _flight(1, "14:00", "15:30", 60.0),
    ]
    bus2 = [
        _bus(0, "16:00", "18:00", 100.0),  # valid for f0 (≥ 15:30)
        _bus(1, "17:31", "19:00", 5.0),    # valid only for f1
    ]

    trio = _default_trio(bus1, flights, bus2, DATE, 2.0)

    assert trio == {"bus1_idx": 0, "bus1_source": "same",
                    "flight_idx": 0,
                    "bus2_idx": 0, "bus2_source": "same"}


def test_default_trio_walks_to_next_flight_when_first_has_no_bus1():
    # First (cheapest) flight is too early for any bus1 to land 2h before.
    bus1 = [_bus(0, "07:00", "09:00", 25.0)]   # arrives 09:00
    flights = [
        _flight(0, "08:00", "09:30", 50.0),    # needs bus1 ≤ 06:00 — none
        _flight(1, "12:00", "13:30", 70.0),    # needs bus1 ≤ 10:00 — bus0 fits
    ]
    bus2 = [_bus(0, "16:00", "18:00", 20.0)]   # 16:00 ≥ 13:30 + 2h = 15:30 ✓
    trio = _default_trio(bus1, flights, bus2, DATE, 2.0)
    assert trio == {"bus1_idx": 0, "bus1_source": "same",
                    "flight_idx": 1,
                    "bus2_idx": 0, "bus2_source": "same"}


def test_default_trio_returns_none_when_no_flight_has_both_buses():
    bus1 = [_bus(0, "07:00", "09:00", 25.0)]
    flights = [_flight(0, "12:00", "13:30", 50.0)]
    bus2 = [_bus(0, "14:00", "16:00", 20.0)]   # 14:00 < 13:30 + 2h = 15:30 — invalid
    trio = _default_trio(bus1, flights, bus2, DATE, 2.0)
    assert trio is None


def test_default_trio_skips_options_with_none_price():
    bus1 = [
        _bus(0, "07:00", "09:00", None),       # priceless — skipped
        _bus(1, "07:30", "09:30", 25.0),
    ]
    flights = [_flight(0, "12:00", "13:30", 60.0)]
    bus2 = [
        _bus(0, "16:00", "18:00", None),       # priceless — skipped
        _bus(1, "16:30", "18:30", 22.0),
    ]
    trio = _default_trio(bus1, flights, bus2, DATE, 2.0)
    assert trio == {"bus1_idx": 1, "bus1_source": "same",
                    "flight_idx": 0,
                    "bus2_idx": 1, "bus2_source": "same"}


def test_default_trio_picks_latest_valid_bus1_and_earliest_valid_bus2():
    # Many valid options on each side — assert tightest connections.
    bus1 = [
        _bus(0, "06:00", "08:00", 30.0),
        _bus(1, "07:00", "09:00", 30.0),
        _bus(2, "08:00", "09:45", 30.0),   # latest ≤ 10:00
        _bus(3, "09:00", "10:30", 30.0),   # invalid: 10:30 > 10:00
    ]
    flights = [_flight(0, "12:00", "13:30", 60.0)]
    bus2 = [
        _bus(0, "20:00", "22:00", 25.0),
        _bus(1, "16:00", "18:00", 25.0),
        _bus(2, "15:35", "17:45", 25.0),   # earliest ≥ 15:30
        _bus(3, "15:25", "17:30", 25.0),   # invalid: 15:25 < 15:30
    ]
    trio = _default_trio(bus1, flights, bus2, DATE, 2.0)
    assert trio == {"bus1_idx": 2, "bus1_source": "same",
                    "flight_idx": 0,
                    "bus2_idx": 2, "bus2_source": "same"}


# ── _min_valid_total_three_legs ──────────────────────────────────────────────

def test_min_valid_total_picks_cheapest_per_leg_independently_of_default():
    # Cheapest flight has expensive valid buses; pricier flight has cheaper
    # valid buses. min_total picks the cheapest *sum*, not the cheapest flight.
    bus1 = [
        _bus(0, "07:00", "09:00", 100.0),  # only valid for f0 (needs ≤ 10:00)
        _bus(1, "08:00", "11:30", 5.0),    # only valid for f1 (≤ 12:00)
    ]
    flights = [
        _flight(0, "12:00", "13:30", 50.0),  # bus1: ≤ 10:00 → only b0 (100); bus2: ≥ 15:30
        _flight(1, "14:00", "15:30", 60.0),  # bus1: ≤ 12:00 → b1 (5); bus2: ≥ 17:30
    ]
    bus2 = [
        _bus(0, "16:00", "18:00", 100.0),  # only valid for f0
        _bus(1, "17:31", "19:00", 5.0),    # only valid for f1
    ]
    # f0 trio: 100 + 50 + 100 = 250
    # f1 trio: 5 + 60 + 5 = 70
    assert _min_valid_total_three_legs(bus1, flights, bus2, DATE, 2.0) == 70.0


def test_min_valid_total_returns_none_when_no_valid_trio():
    bus1 = [_bus(0, "07:00", "09:00", 25.0)]
    flights = [_flight(0, "10:00", "11:30", 60.0)]
    bus2 = [_bus(0, "12:00", "14:00", 20.0)]   # 12:00 < 11:30 + 2h = 13:30 — invalid
    assert _min_valid_total_three_legs(bus1, flights, bus2, DATE, 2.0) is None


def test_min_valid_total_handles_empty_lists():
    assert _min_valid_total_three_legs([], [], [], DATE, 2.0) is None
    flight = [_flight(0, "10:00", "11:30", 60.0)]
    assert _min_valid_total_three_legs([], flight, [], DATE, 2.0) is None


# ── overnight handling ──────────────────────────────────────────────────────

PREV_DATE = "2026-05-09"  # DATE - 1 day
NEXT_DATE = "2026-05-11"  # DATE + 1 day


def test_default_trio_uses_prev_day_bus_when_cheap_flight_only_reachable_overnight():
    # No same-day bus arrives by 04:00 (deadline for the 06:00 flight). A
    # prev-day bus arriving 23:30 the night before unlocks the cheapest
    # flight, which is the explicit user ask ("Milan→Nuremberg €46 path").
    bus1_same = [_bus(0, "08:00", "10:00", 5.0)]
    bus1_prev = [_bus(0, "20:00", "23:30", 5.0,
                      date=PREV_DATE, arr_date=PREV_DATE)]
    flights = [
        _flight(0, "06:00", "07:30", 20.0),  # cheapest, prev-day needed
        _flight(1, "14:00", "15:30", 60.0),
    ]
    bus2 = [_bus(0, "09:30", "12:00", 5.0)]

    trio = _default_trio(
        bus1_same, flights, bus2, DATE, 2.0,
        bus1_prev_options=bus1_prev,
    )
    assert trio == {"bus1_idx": 0, "bus1_source": "prev",
                    "flight_idx": 0,
                    "bus2_idx": 0, "bus2_source": "same"}


def test_default_trio_prefers_same_day_when_tied_with_overnight():
    # Same-day and prev-day buses both arrive at 08:00 — selector must
    # break the tie toward same-day so we don't surface an overnight stay
    # when same-day equally satisfies the 2h rule.
    bus1_same = [_bus(0, "06:00", "08:00", 10.0)]
    bus1_prev = [_bus(0, "20:00", "08:00", 5.0,
                      date=PREV_DATE, arr_date=DATE)]
    flights = [_flight(0, "10:00", "11:30", 60.0)]
    bus2 = [_bus(0, "13:30", "15:30", 10.0)]

    trio = _default_trio(
        bus1_same, flights, bus2, DATE, 2.0,
        bus1_prev_options=bus1_prev,
    )
    assert trio == {"bus1_idx": 0, "bus1_source": "same",
                    "flight_idx": 0,
                    "bus2_idx": 0, "bus2_source": "same"}


def test_default_trio_uses_next_day_bus_for_late_flight():
    bus1 = [_bus(0, "06:00", "08:00", 5.0)]
    flights = [
        _flight(0, "22:00", "23:30", 20.0),  # late + cheap, no same-day bus2 after 01:30
        _flight(1, "10:00", "11:30", 60.0),
    ]
    bus2_same = [_bus(0, "13:30", "15:00", 5.0)]
    bus2_next = [_bus(0, "07:00", "09:00", 5.0,
                       date=NEXT_DATE, arr_date=NEXT_DATE)]

    trio = _default_trio(
        bus1, flights, bus2_same, DATE, 2.0,
        bus2_next_options=bus2_next,
    )
    assert trio == {"bus1_idx": 0, "bus1_source": "same",
                    "flight_idx": 0,
                    "bus2_idx": 0, "bus2_source": "next"}


# ── overnight-query gate ────────────────────────────────────────────────────

def test_comfort_before_flight_true_when_bus_arrives_with_3h_slack():
    # Flight at 10:00, bus arrives 07:00 → 3h gap exactly meets _COMFORT_HOURS.
    flight = _flight(0, "10:00", "11:30", 50.0)
    bus = [_bus(0, "05:00", "07:00", 10.0)]
    assert _has_comfortable_bus_before_flight(flight, bus, DATE) is True


def test_comfort_before_flight_false_when_only_2h_slack():
    # 2h gap satisfies hard transfer rule but NOT comfort threshold → still query overnight.
    flight = _flight(0, "10:00", "11:30", 50.0)
    bus = [_bus(0, "06:00", "08:00", 10.0)]
    assert _has_comfortable_bus_before_flight(flight, bus, DATE) is False


def test_comfort_before_flight_false_when_no_priced_bus():
    flight = _flight(0, "10:00", "11:30", 50.0)
    bus = [_bus(0, "05:00", "07:00", None)]   # priceless → ignored
    assert _has_comfortable_bus_before_flight(flight, bus, DATE) is False


def test_comfort_before_flight_false_when_empty_bus_list():
    flight = _flight(0, "10:00", "11:30", 50.0)
    assert _has_comfortable_bus_before_flight(flight, [], DATE) is False


def test_comfort_after_flight_true_when_bus_departs_with_3h_slack():
    flight = _flight(0, "08:00", "09:30", 50.0)
    bus = [_bus(0, "12:30", "14:30", 10.0)]   # 12:30 ≥ 09:30 + 3h
    assert _has_comfortable_bus_after_flight(flight, bus, DATE) is True


def test_comfort_after_flight_false_when_only_2h_slack():
    flight = _flight(0, "08:00", "09:30", 50.0)
    bus = [_bus(0, "11:30", "13:30", 10.0)]   # 11:30 = 09:30 + 2h (no comfort)
    assert _has_comfortable_bus_after_flight(flight, bus, DATE) is False


def test_comfort_threshold_constant_matches_user_spec():
    # User explicitly asked for a 3h comfort window — guard the constant so
    # silent edits don't change the gate behavior.
    assert _COMFORT_HOURS == 3.0


def test_min_valid_total_three_legs_considers_overnight_buses():
    bus1_same = [_bus(0, "07:00", "09:00", 100.0)]  # only valid for f1
    bus1_prev = [_bus(0, "20:00", "23:30", 3.0,
                      date=PREV_DATE, arr_date=PREV_DATE)]
    flights = [
        _flight(0, "06:00", "07:30", 20.0),
        _flight(1, "14:00", "15:30", 60.0),
    ]
    bus2 = [_bus(0, "09:30", "12:00", 5.0)]
    # f0 trio: 3 + 20 + 5 = 28 (uses prev-day bus1)
    # f1 trio: 100 + 60 + 5 = 165
    assert _min_valid_total_three_legs(
        bus1_same, flights, bus2, DATE, 2.0,
        bus1_prev_options=bus1_prev,
    ) == 28.0
