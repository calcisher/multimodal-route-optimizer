"""Tests for input validation (_resolve_inputs date guard) and /api/fx."""
from __future__ import annotations

from datetime import date, timedelta

import app as app_module


def _client():
    return app_module.app.test_client()


def _post_flights(date_str: str):
    return _client().post(
        "/api/flights",
        json={"from_city": "Milan", "to_city": "Nuremberg", "date": date_str},
    )


def test_past_date_rejected_with_400():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    response = _post_flights(yesterday)
    assert response.status_code == 400
    assert "past" in response.get_json()["error"]


def test_malformed_date_rejected_with_400():
    response = _post_flights("20.05.2026")
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.get_json()["error"]


def test_missing_date_rejected_with_400():
    response = _client().post(
        "/api/flights", json={"from_city": "Milan", "to_city": "Nuremberg"}
    )
    assert response.status_code == 400


def test_today_passes_date_guard(monkeypatch):
    # Today must NOT be rejected. Stop before any SerpAPI call by failing
    # resolve_iata — a 400 mentioning airports proves the date guard passed.
    monkeypatch.setattr(app_module, "resolve_iata", lambda _q: None)
    response = _post_flights(date.today().isoformat())
    assert response.status_code == 400
    assert "Could not resolve airports" in response.get_json()["error"]


def test_fx_serves_fallback_when_fetch_fails(monkeypatch):
    class _Boom:
        @staticmethod
        def get(*_a, **_k):
            raise RuntimeError("network down")

    monkeypatch.setattr(app_module, "httpx", _Boom)
    monkeypatch.setattr(app_module, "_fx_cache", {})
    monkeypatch.setattr(app_module, "_fx_fetched_at", None)

    response = _client().get("/api/fx")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["stale"] is True
    assert payload["rates"]["EUR"] == 1
    assert set(payload["rates"]) >= {"EUR", "USD", "GBP", "TRY"}


def test_fx_caches_successful_fetch(monkeypatch):
    calls = {"n": 0}

    class _Resp:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {"date": "2026-06-10", "rates": {"USD": 1.15, "GBP": 0.86, "TRY": 53.2}}

    class _Httpx:
        @staticmethod
        def get(*_a, **_k):
            calls["n"] += 1
            return _Resp()

    monkeypatch.setattr(app_module, "httpx", _Httpx)
    monkeypatch.setattr(app_module, "_fx_cache", {})
    monkeypatch.setattr(app_module, "_fx_fetched_at", None)

    client = _client()
    first = client.get("/api/fx").get_json()
    second = client.get("/api/fx").get_json()

    assert calls["n"] == 1  # second hit served from cache
    assert first == second
    assert first["rates"]["EUR"] == 1.0
    assert first["rates"]["TRY"] == 53.2
    assert "stale" not in first
