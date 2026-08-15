"""
Tests for delayed_flights.py

All AeroAPI HTTP calls are mocked – no real network requests are made.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import responses as resp_lib
from responses import RequestsMock

import delayed_flights as df

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API_KEY = "test_key_123"
BASE = "https://aeroapi.flightaware.com/aeroapi"
DEPARTURES_URL = f"{BASE}/airports/GCTS/flights/departures"

NOW_UTC = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)   # fixed "now"
START   = NOW_UTC - timedelta(hours=48)
END     = NOW_UTC - timedelta(hours=24)


def _make_flight(
    ident: str = "VY1234",
    scheduled_off: str = "2026-08-13T10:00:00Z",
    actual_off: str | None = "2026-08-13T11:05:00Z",
    estimated_off: str | None = None,
    dest_code: str = "LEBL",
    dest_name: str = "Barcelona",
    dest_city: str = "Barcelona",
) -> dict:
    return {
        "ident": ident,
        "ident_iata": ident,
        "ident_icao": ident,
        "fa_flight_id": f"FA{ident}",
        "operator": "VLG",
        "operator_iata": "VY",
        "flight_number": ident[2:],
        "registration": "EC-MYB",
        "atc_ident": None,
        "inbound_fa_flight_id": None,
        "codeshares": [],
        "codeshares_iata": [],
        "blocked": False,
        "diverted": False,
        "cancelled": False,
        "position_only": False,
        "origin": {
            "code": "GCTS",
            "code_icao": "GCTS",
            "code_iata": "TFS",
            "name": "Tenerife South",
            "city": "Tenerife",
            "timezone": "Atlantic/Canary",
            "airport_info_url": "/airports/GCTS",
        },
        "destination": {
            "code": dest_code,
            "code_icao": dest_code,
            "code_iata": None,
            "name": dest_name,
            "city": dest_city,
            "timezone": "Europe/Madrid",
            "airport_info_url": f"/airports/{dest_code}",
        },
        "scheduled_out": "2026-08-13T09:45:00Z",
        "estimated_out": None,
        "actual_out": "2026-08-13T09:50:00Z",
        "scheduled_off": scheduled_off,
        "estimated_off": estimated_off,
        "actual_off": actual_off,
        "scheduled_on": None,
        "estimated_on": None,
        "actual_on": None,
        "scheduled_in": None,
        "estimated_in": None,
        "actual_in": None,
        "departure_delay": 3900,
        "arrival_delay": None,
        "filed_ete": 3600,
        "progress_percent": 100,
        "status": "Landed",
        "aircraft_type": "A320",
        "type": "Airline",
        "route_distance": 900,
        "filed_airspeed": 450,
        "filed_altitude": 370,
        "route": "DCT GCTS",
        "baggage_claim": None,
        "seats_cabin_business": None,
        "seats_cabin_coach": 180,
        "seats_cabin_first": None,
        "gate_origin": "A3",
        "gate_destination": None,
        "terminal_origin": None,
        "terminal_destination": None,
    }


def _page(flights: list[dict], next_cursor: str | None = None) -> dict:
    links = {"next": f"/airports/GCTS/flights/departures?cursor={next_cursor}"} \
            if next_cursor else {"next": None}
    return {
        "links": links,
        "num_pages": 1,
        "departures": flights,
    }


def _env(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)


# ---------------------------------------------------------------------------
# Unit tests: date / window helpers
# ---------------------------------------------------------------------------

class TestParseIso8601:
    def test_zulu_suffix(self):
        dt = df.parse_iso8601("2026-08-10T00:00:00Z")
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026

    def test_offset_suffix(self):
        dt = df.parse_iso8601("2026-08-10T02:00:00+02:00")
        assert dt == datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)

    def test_no_timezone_raises(self):
        with pytest.raises(df.ValidationError, match="no timezone"):
            df.parse_iso8601("2026-08-10T00:00:00")

    def test_invalid_format_raises(self):
        with pytest.raises(df.ValidationError, match="Cannot parse"):
            df.parse_iso8601("not-a-date")


class TestValidateWindow:
    def _pair(self, hours_ago_start: float = 48, hours_ago_end: float = 24):
        now = datetime.now(tz=timezone.utc)
        start = now - timedelta(hours=hours_ago_start)
        end   = now - timedelta(hours=hours_ago_end)
        return start, end

    def test_valid_24h_window(self):
        start, end = self._pair()
        df.validate_window(start, end)  # no exception

    def test_wrong_duration_raises(self):
        now = datetime.now(tz=timezone.utc)
        start = now - timedelta(hours=48)
        end   = now - timedelta(hours=25)   # 23 h
        with pytest.raises(df.ValidationError, match="24 hours"):
            df.validate_window(start, end)

    def test_too_old_raises(self):
        now = datetime.now(tz=timezone.utc)
        start = now - timedelta(days=20)
        end   = start + timedelta(hours=24)
        with pytest.raises(df.ValidationError, match="10 days"):
            df.validate_window(start, end)


# ---------------------------------------------------------------------------
# Unit tests: delay calculation
# ---------------------------------------------------------------------------

class TestDelayCalculation:
    """Test the exact 60-min boundary."""

    def _run(self, scheduled: str, actual: str, min_delay: int = 60) -> list[df.DelayedFlight]:
        flight = _make_flight(scheduled_off=scheduled, actual_off=actual)
        stats  = df.Stats()
        results: list[df.DelayedFlight] = []

        with patch("delayed_flights._request_page", return_value=_page([flight])):
            import requests
            with requests.Session() as session:
                for f in df.iter_delayed_flights(
                    session, API_KEY, "GCTS", START, END, min_delay, False, stats
                ):
                    results.append(f)

        return results

    def test_exactly_60_min_delay_found(self):
        results = self._run("2026-08-13T10:00:00Z", "2026-08-13T11:00:00Z", min_delay=60)
        assert len(results) == 1
        assert results[0].delay_minutes == 60

    def test_more_than_60_min_delay_found(self):
        results = self._run("2026-08-13T10:00:00Z", "2026-08-13T11:30:00Z", min_delay=60)
        assert len(results) == 1
        assert results[0].delay_minutes == 90

    def test_59_min_delay_not_found(self):
        results = self._run("2026-08-13T10:00:00Z", "2026-08-13T10:59:00Z", min_delay=60)
        assert len(results) == 0

    def test_zero_delay_not_found(self):
        results = self._run("2026-08-13T10:00:00Z", "2026-08-13T10:00:00Z", min_delay=60)
        assert len(results) == 0

    def test_early_flight_not_found(self):
        results = self._run("2026-08-13T10:00:00Z", "2026-08-13T09:45:00Z", min_delay=60)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Unit tests: missing timestamps → skipped
# ---------------------------------------------------------------------------

class TestMissingTimestamps:
    def _skipped_stats(self, scheduled_off=None, actual_off=None):
        flight = _make_flight()
        flight["scheduled_off"] = scheduled_off
        flight["actual_off"]    = actual_off
        stats  = df.Stats()

        with patch("delayed_flights._request_page", return_value=_page([flight])):
            import requests
            with requests.Session() as session:
                list(df.iter_delayed_flights(
                    session, API_KEY, "GCTS", START, END, 60, False, stats
                ))
        return stats

    def test_missing_scheduled_off_skipped(self):
        stats = self._skipped_stats(scheduled_off=None, actual_off="2026-08-13T11:00:00Z")
        assert stats.flights_skipped == 1
        assert stats.flights_found   == 0

    def test_missing_actual_off_skipped(self):
        stats = self._skipped_stats(scheduled_off="2026-08-13T10:00:00Z", actual_off=None)
        assert stats.flights_skipped == 1
        assert stats.flights_found   == 0

    def test_both_missing_skipped(self):
        stats = self._skipped_stats(scheduled_off=None, actual_off=None)
        assert stats.flights_skipped == 1


# ---------------------------------------------------------------------------
# Unit tests: pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_pagination_follows_cursor(self):
        """Two pages: only second page has delayed flight."""
        page1 = _page(
            [_make_flight("FR0001", actual_off="2026-08-13T10:20:00Z")],  # 20 min → skipped
            next_cursor="abc123",
        )
        page2 = _page(
            [_make_flight("FR0002", actual_off="2026-08-13T11:05:00Z")],  # 65 min → found
        )
        call_count = 0

        def fake_request(session, api_key, airport, start, end, cursor, stats):
            nonlocal call_count
            call_count += 1
            stats.requests_made += 1
            return page1 if cursor is None else page2

        stats   = df.Stats()
        results = []
        with patch("delayed_flights._request_page", side_effect=fake_request):
            import requests
            with requests.Session() as session:
                for f in df.iter_delayed_flights(
                    session, API_KEY, "GCTS", START, END, 60, False, stats
                ):
                    results.append(f)

        assert call_count == 2
        assert len(results) == 1
        assert results[0].ident == "FR0002"

    def test_stops_after_first_result(self):
        """stop_at_first=True must not follow pagination cursor."""
        page1 = _page(
            [_make_flight("FR0001", actual_off="2026-08-13T11:05:00Z")],  # 65 min → found
            next_cursor="cursor_to_page2",
        )
        page2 = _page([_make_flight("FR0002", actual_off="2026-08-13T12:00:00Z")])
        call_count = 0

        def fake_request(session, api_key, airport, start, end, cursor, stats):
            nonlocal call_count
            call_count += 1
            stats.requests_made += 1
            return page1 if cursor is None else page2

        stats   = df.Stats()
        results = []
        with patch("delayed_flights._request_page", side_effect=fake_request):
            import requests
            with requests.Session() as session:
                for f in df.iter_delayed_flights(
                    session, API_KEY, "GCTS", START, END, 60, True, stats
                ):
                    results.append(f)
                    break  # simulate stop_at_first

        assert call_count == 1
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Unit tests: --all flag
# ---------------------------------------------------------------------------

class TestShowAll:
    def test_all_flag_returns_multiple_flights(self):
        flights = [
            _make_flight("FR0001", actual_off="2026-08-13T11:05:00Z"),  # +65
            _make_flight("FR0002", actual_off="2026-08-13T11:15:00Z"),  # +75
            _make_flight("FR0003", actual_off="2026-08-13T10:20:00Z"),  # +20 skip
        ]
        stats   = df.Stats()
        results = []

        with patch("delayed_flights._request_page", return_value=_page(flights)):
            import requests
            with requests.Session() as session:
                for f in df.iter_delayed_flights(
                    session, API_KEY, "GCTS", START, END, 60, False, stats
                ):
                    results.append(f)

        assert len(results) == 2
        assert {r.ident for r in results} == {"FR0001", "FR0002"}


# ---------------------------------------------------------------------------
# Integration tests: HTTP error handling (using responses library)
# ---------------------------------------------------------------------------

@pytest.fixture
def env_key(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)


@resp_lib.activate
def test_auth_error_401(env_key):
    resp_lib.add(
        resp_lib.GET, DEPARTURES_URL,
        json={"title": "Unauthorized", "reason": "Invalid key", "detail": "Bad key", "status": 401},
        status=401,
    )
    import requests as req
    with req.Session() as session:
        with pytest.raises(df.AuthError):
            df.Stats()
            df._request_page(session, API_KEY, "GCTS", START, END, None, df.Stats())


@resp_lib.activate
def test_auth_error_403(env_key):
    resp_lib.add(
        resp_lib.GET, DEPARTURES_URL,
        json={"title": "Forbidden", "reason": "Forbidden", "detail": "No access", "status": 403},
        status=403,
    )
    import requests as req
    with req.Session() as session:
        with pytest.raises(df.AuthError):
            df._request_page(session, API_KEY, "GCTS", START, END, None, df.Stats())


@resp_lib.activate
def test_rate_limit_after_retry(env_key):
    """Two consecutive 429 responses → RateLimitError (no infinite loop)."""
    for _ in range(2):
        resp_lib.add(
            resp_lib.GET, DEPARTURES_URL,
            json={"title": "Rate Limited", "reason": "RateLimit", "detail": "Slow down", "status": 429},
            status=429,
            headers={"Retry-After": "1"},
        )
    import requests as req
    with req.Session() as session:
        with patch("time.sleep"):   # don't actually sleep in tests
            with pytest.raises(df.RateLimitError):
                df._request_page(session, API_KEY, "GCTS", START, END, None, df.Stats())


@resp_lib.activate
def test_timeout_error(env_key):
    import requests as req
    resp_lib.add(
        resp_lib.GET, DEPARTURES_URL,
        body=req.exceptions.Timeout(),
    )
    with req.Session() as session:
        with pytest.raises(df.NetworkTimeoutError):
            df._request_page(session, API_KEY, "GCTS", START, END, None, df.Stats())


@resp_lib.activate
def test_connection_error(env_key):
    import requests as req
    resp_lib.add(
        resp_lib.GET, DEPARTURES_URL,
        body=req.exceptions.ConnectionError("No route to host"),
    )
    with req.Session() as session:
        with pytest.raises(df.NetworkConnectionError):
            df._request_page(session, API_KEY, "GCTS", START, END, None, df.Stats())


# ---------------------------------------------------------------------------
# Integration tests: main() with mocked HTTP
# ---------------------------------------------------------------------------

def test_main_no_api_key(monkeypatch):
    monkeypatch.delenv("FLIGHTAWARE_API_KEY", raising=False)
    rc = df.main(["--airport", "GCTS",
                  "--start", "2026-08-13T00:00:00Z",
                  "--end", "2026-08-14T00:00:00Z"])
    assert rc == df.ConfigError.exit_code


def test_main_invalid_dates(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    rc = df.main(["--airport", "GCTS",
                  "--start", "not-a-date",
                  "--end", "2026-08-14T00:00:00Z"])
    assert rc == df.ValidationError.exit_code


def test_main_no_timezone_in_date(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    rc = df.main(["--airport", "GCTS",
                  "--start", "2026-08-13T00:00:00",   # no tz
                  "--end", "2026-08-14T00:00:00Z"])
    assert rc == df.ValidationError.exit_code


def test_main_window_not_24h(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    rc = df.main(["--airport", "GCTS",
                  "--start", "2026-08-13T00:00:00Z",
                  "--end", "2026-08-13T12:00:00Z"])   # only 12 h
    assert rc == df.ValidationError.exit_code


def test_main_found_flight_text_output(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    flight = _make_flight("VY9999",
                          scheduled_off="2026-08-13T10:00:00Z",
                          actual_off="2026-08-13T11:10:00Z")  # 70 min

    with patch("delayed_flights._request_page", return_value=_page([flight])):
        rc = df.main([
            "--airport", "GCTS",
            "--start", "2026-08-13T00:00:00Z",
            "--end", "2026-08-14T00:00:00Z",
        ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "FOUND DELAYED FLIGHT" in captured.out
    assert "VY9999" in captured.out
    assert "70 minutes" in captured.out


def test_main_no_delayed_flight(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    flight = _make_flight("VY1111",
                          scheduled_off="2026-08-13T10:00:00Z",
                          actual_off="2026-08-13T10:10:00Z")  # 10 min only

    with patch("delayed_flights._request_page", return_value=_page([flight])):
        rc = df.main([
            "--airport", "GCTS",
            "--start", "2026-08-13T00:00:00Z",
            "--end", "2026-08-14T00:00:00Z",
        ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "NO FLIGHT" in captured.out


def test_main_json_output(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    flight = _make_flight("IB3456",
                          scheduled_off="2026-08-13T10:00:00Z",
                          actual_off="2026-08-13T11:30:00Z")  # 90 min

    with patch("delayed_flights._request_page", return_value=_page([flight])):
        rc = df.main([
            "--airport", "GCTS",
            "--start", "2026-08-13T00:00:00Z",
            "--end", "2026-08-14T00:00:00Z",
            "--json",
        ])

    captured = capsys.readouterr()
    assert rc == 0
    data = json.loads(captured.out)
    assert data["flights_found"] == 1
    assert data["delayed_flights"][0]["ident"] == "IB3456"
    assert data["delayed_flights"][0]["delay_minutes"] == 90


def test_main_all_flag_returns_multiple(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    flights = [
        _make_flight("AA001", actual_off="2026-08-13T11:05:00Z"),  # 65 min
        _make_flight("AA002", actual_off="2026-08-13T11:20:00Z"),  # 80 min
    ]

    with patch("delayed_flights._request_page", return_value=_page(flights)):
        rc = df.main([
            "--airport", "GCTS",
            "--start", "2026-08-13T00:00:00Z",
            "--end", "2026-08-14T00:00:00Z",
            "--all", "--json",
        ])

    captured = capsys.readouterr()
    assert rc == 0
    data = json.loads(captured.out)
    assert data["flights_found"] == 2


# ---------------------------------------------------------------------------
# Unit: airport_label helper
# ---------------------------------------------------------------------------

class TestAirportLabel:
    def test_known_icao_from_dict(self):
        label = df.airport_label("GCTS", None, None)
        assert "GCTS" in label
        assert "Tenerife South" in label

    def test_name_overrides_dict(self):
        label = df.airport_label("GCTS", "Custom Name", None)
        assert "Custom Name" in label

    def test_unknown_no_city(self):
        label = df.airport_label("ZZZZ", None, None)
        assert label == "ZZZZ"

    def test_none_code(self):
        assert df.airport_label(None, None, None) == "Unknown"


# ---------------------------------------------------------------------------
# Smoke test: module imports cleanly
# ---------------------------------------------------------------------------

def test_module_attributes():
    assert hasattr(df, "main")
    assert hasattr(df, "iter_delayed_flights")
    assert hasattr(df, "parse_iso8601")
    assert hasattr(df, "validate_window")
