from __future__ import annotations


def test_bus_flight_bus_endpoint_includes_endpoint_city_coords(monkeypatch):
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "_resolve_inputs",
        lambda _payload: (
            ("MXP", "NUE", "Milan", "Nuremberg", "2026-05-20", ["MXP"], ["NUE"]),
            None,
        ),
    )
    monkeypatch.setattr(app_module, "find_cheap_bus_plus_flight_plus_bus", lambda **_kwargs: [])
    monkeypatch.setattr(
        app_module,
        "_city_coords",
        lambda city: {"lat": 45.4642, "lon": 9.19}
        if city == "Milan"
        else {"lat": 49.4521, "lon": 11.0767},
    )

    client = app_module.app.test_client()
    response = client.post("/api/bus-flight-bus", json={"from_city": "Milan"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["fromCity"] == "Milan"
    assert payload["toCity"] == "Nuremberg"
    assert payload["fromCoords"] == {"lat": 45.4642, "lon": 9.19}
    assert payload["toCoords"] == {"lat": 49.4521, "lon": 11.0767}
