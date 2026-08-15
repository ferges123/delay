"""
Tests for delayed_flights.py v0.0.1

All AeroAPI HTTP calls are mocked – no real network requests.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import responses as resp_lib

import delayed_flights as df

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

API_KEY = "test_key_123"
BASE    = "https://aeroapi.flightaware.com/aeroapi"
PAST_URL     = f"{BASE}/airports/WAW/flights/departures"
UPCOMING_URL = f"{BASE}/airports/WAW/flights/scheduled_departures"

NOW_UTC = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
START   = NOW_UTC - timedelta(hours=24)
END     = NOW_UTC


def _make_raw_flight(
    ident: str = "LO123",
    scheduled_off: str = "2026-08-14T10:00:00Z",
    actual_off:    str | None = "2026-08-14T11:05:00Z",
    estimated_off: str | None = None,
    departure_delay: int | None = None,   # seconds; for upcoming mode
    dest_code: str = "EGSS",
) -> dict:
    return {
        "ident":          ident,
        "ident_iata":     ident,
        "ident_icao":     ident,
        "fa_flight_id":   f"FA{ident}",
        "operator":       "LOT",
        "operator_iata":  "LO",
        "flight_number":  ident[2:],
        "registration":   "SP-LRA",
        "atc_ident":      None,
        "inbound_fa_flight_id": None,
        "codeshares":     [],
        "codeshares_iata":[],
        "blocked":        False,
        "diverted":       False,
        "cancelled":      False,
        "position_only":  False,
        "origin": {
            "code":      "WAW",
            "code_icao": "EPWA",
            "code_iata": "WAW",
            "name":      "Warsaw Chopin",
            "city":      "Warsaw",
            "timezone":  "Europe/Warsaw",
            "airport_info_url": "/airports/EPWA",
        },
        "destination": {
            "code":      dest_code,
            "code_icao": dest_code,
            "code_iata": None,
            "name":      "London Stansted",
            "city":      "London",
            "timezone":  "Europe/London",
            "airport_info_url": f"/airports/{dest_code}",
        },
        "scheduled_out":  "2026-08-14T09:45:00Z",
        "estimated_out":  None,
        "actual_out":     None,
        "scheduled_off":  scheduled_off,
        "estimated_off":  estimated_off,
        "actual_off":     actual_off,
        "scheduled_on":   None,
        "estimated_on":   None,
        "actual_on":      None,
        "scheduled_in":   None,
        "estimated_in":   None,
        "actual_in":      None,
        "departure_delay": departure_delay,
        "arrival_delay":  None,
        "filed_ete":      3600,
        "progress_percent": 100,
        "status":         "Landed",
        "aircraft_type":  "B738",
        "type":           "Airline",
        "route_distance": 1400,
        "filed_airspeed": 460,
        "filed_altitude": 370,
        "route":          "DCT",
        "baggage_claim":  None,
        "seats_cabin_business": None,
        "seats_cabin_coach": 180,
        "seats_cabin_first": None,
        "gate_origin":    "B5",
        "gate_destination": None,
        "terminal_origin": None,
        "terminal_destination": None,
    }


def _page(flights: list[dict], key: str = "departures", next_cursor: str | None = None) -> dict:
    links = {"next": f"/airports/WAW/flights?cursor={next_cursor}"} if next_cursor else {"next": None}
    return {"links": links, "num_pages": 1, key: flights}


@pytest.fixture
def env_key(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)


# ---------------------------------------------------------------------------
# parse_iso8601 / validate_past_window
# ---------------------------------------------------------------------------

class TestParseIso8601:
    def test_zulu(self):
        dt = df.parse_iso8601("2026-08-10T00:00:00Z")
        assert dt.tzinfo == timezone.utc

    def test_offset(self):
        dt = df.parse_iso8601("2026-08-10T02:00:00+02:00")
        assert dt == datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)

    def test_no_tz_raises(self):
        with pytest.raises(df.ValidationError, match="no timezone"):
            df.parse_iso8601("2026-08-10T00:00:00")

    def test_invalid_raises(self):
        with pytest.raises(df.ValidationError, match="Cannot parse"):
            df.parse_iso8601("not-a-date")


class TestValidatePastWindow:
    def test_valid_24h(self):
        now = datetime.now(tz=timezone.utc)
        df.validate_past_window(now - timedelta(hours=24), now)

    def test_wrong_duration(self):
        now = datetime.now(tz=timezone.utc)
        with pytest.raises(df.ValidationError, match="24 hours"):
            df.validate_past_window(now - timedelta(hours=12), now)

    def test_too_old(self):
        now = datetime.now(tz=timezone.utc)
        with pytest.raises(df.ValidationError, match="10 days"):
            df.validate_past_window(now - timedelta(days=20), now - timedelta(days=19))


# ---------------------------------------------------------------------------
# fmt_local
# ---------------------------------------------------------------------------

class TestFmtLocal:
    BASE = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)

    def test_no_tz_utc_only(self):
        r = df.fmt_local(self.BASE, None)
        assert "12:00 UTC" in r
        assert "(" not in r

    def test_known_tz(self):
        r = df.fmt_local(self.BASE, "Europe/Warsaw")
        assert "12:00 UTC" in r
        assert "14:00" in r   # CEST = UTC+2

    def test_unknown_tz_fallback(self):
        r = df.fmt_local(self.BASE, "Bogus/Invalid")
        assert "12:00 UTC" in r

    def test_none_dt(self):
        assert df.fmt_local(None, "Europe/Warsaw") == "N/A"


# ---------------------------------------------------------------------------
# _build_flight – past mode (actual delay)
# ---------------------------------------------------------------------------

class TestBuildFlightPast:
    def test_exact_60_min(self):
        raw = _make_raw_flight(scheduled_off="2026-08-14T10:00:00Z",
                               actual_off="2026-08-14T11:00:00Z")
        f = df._build_flight(raw, "WAW", is_past=True)
        assert f is not None
        assert f.delay_minutes == 60

    def test_over_60_min(self):
        raw = _make_raw_flight(scheduled_off="2026-08-14T10:00:00Z",
                               actual_off="2026-08-14T11:30:00Z")
        f = df._build_flight(raw, "WAW", is_past=True)
        assert f is not None and f.delay_minutes == 90

    def test_early_flight_negative(self):
        raw = _make_raw_flight(scheduled_off="2026-08-14T10:00:00Z",
                               actual_off="2026-08-14T09:50:00Z")
        f = df._build_flight(raw, "WAW", is_past=True)
        assert f is not None and f.delay_minutes == -10

    def test_missing_actual_off_returns_none(self):
        raw = _make_raw_flight(actual_off=None)
        f = df._build_flight(raw, "WAW", is_past=True)
        assert f is None

    def test_missing_scheduled_off_returns_none(self):
        raw = _make_raw_flight(scheduled_off=None, actual_off="2026-08-14T11:00:00Z")
        raw["scheduled_off"] = None
        f = df._build_flight(raw, "WAW", is_past=True)
        assert f is None

    def test_timezone_stored(self):
        raw = _make_raw_flight(scheduled_off="2026-08-14T10:00:00Z",
                               actual_off="2026-08-14T11:05:00Z")
        f = df._build_flight(raw, "WAW", is_past=True)
        assert f is not None
        assert f.origin_tz == "Europe/Warsaw"
        assert f.destination_tz == "Europe/London"


# ---------------------------------------------------------------------------
# _build_flight – upcoming mode (departure_delay)
# ---------------------------------------------------------------------------

class TestBuildFlightUpcoming:
    def test_departure_delay_field(self):
        raw = _make_raw_flight(actual_off=None, departure_delay=4200)  # 70 min
        f = df._build_flight(raw, "WAW", is_past=False)
        assert f is not None
        assert f.delay_minutes == 70

    def test_no_departure_delay_returns_none(self):
        raw = _make_raw_flight(actual_off=None, departure_delay=None)
        raw["departure_delay"] = None
        f = df._build_flight(raw, "WAW", is_past=False)
        assert f is None

    def test_is_past_false(self):
        raw = _make_raw_flight(actual_off=None, departure_delay=3600)
        f = df._build_flight(raw, "WAW", is_past=False)
        assert f is not None and f.is_past is False


# ---------------------------------------------------------------------------
# iter_past_delayed / iter_upcoming_delayed – pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_past_follows_cursor(self):
        page1 = _page([_make_raw_flight("LO001", actual_off="2026-08-14T10:20:00Z")],
                      key="departures", next_cursor="cur1")   # 20 min → skipped
        page2 = _page([_make_raw_flight("LO002", actual_off="2026-08-14T11:05:00Z")],
                      key="departures")                        # 65 min → found

        call_n = 0
        def fake(session, api_key, url, params, stats):
            nonlocal call_n
            call_n += 1
            stats.requests_made += 1
            return page1 if call_n == 1 else page2

        import requests
        stats = df.Stats()
        with patch("delayed_flights._request_page", side_effect=fake):
            with requests.Session() as s:
                results = list(df.iter_past_delayed(
                    s, API_KEY, "WAW", START, END, 60, False, stats))

        assert call_n == 2
        assert len(results) == 1
        assert results[0].ident == "LO002"

    def test_stops_after_first_past(self):
        page1 = _page([_make_raw_flight("LO001", actual_off="2026-08-14T11:05:00Z")],
                      key="departures", next_cursor="cur1")

        call_n = 0
        def fake(session, api_key, url, params, stats):
            nonlocal call_n
            call_n += 1
            stats.requests_made += 1
            return page1

        import requests
        stats = df.Stats()
        with patch("delayed_flights._request_page", side_effect=fake):
            with requests.Session() as s:
                results = []
                for f in df.iter_past_delayed(s, API_KEY, "WAW", START, END, 60, True, stats):
                    results.append(f)
                    break

        assert call_n == 1
        assert len(results) == 1

    def test_upcoming_uses_scheduled_departures_key(self):
        now = datetime.now(tz=timezone.utc)
        page = _page(
            [_make_raw_flight("LO999", actual_off=None, departure_delay=4200)],
            key="scheduled_departures",
        )

        import requests
        stats = df.Stats()
        with patch("delayed_flights._request_page", return_value=page):
            with requests.Session() as s:
                results = list(df.iter_upcoming_delayed(
                    s, API_KEY, "WAW", now, now + timedelta(hours=6), 60, False, stats))

        assert len(results) == 1
        assert results[0].delay_minutes == 70
        assert results[0].is_past is False


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_auth_401(env_key):
    resp_lib.add(resp_lib.GET, PAST_URL,
                 json={"title": "Unauthorized", "reason": "x", "detail": "bad key", "status": 401},
                 status=401)
    import requests as req
    with req.Session() as s:
        with pytest.raises(df.AuthError):
            df._request_page(s, API_KEY, PAST_URL, {}, df.Stats())


@resp_lib.activate
def test_rate_limit_twice(env_key):
    for _ in range(2):
        resp_lib.add(resp_lib.GET, PAST_URL,
                     json={"title": "Rate", "reason": "x", "detail": "slow", "status": 429},
                     status=429, headers={"Retry-After": "1"})
    import requests as req
    with req.Session() as s:
        with patch("time.sleep"):
            with pytest.raises(df.RateLimitError):
                df._request_page(s, API_KEY, PAST_URL, {}, df.Stats())


@resp_lib.activate
def test_timeout(env_key):
    import requests as req
    resp_lib.add(resp_lib.GET, PAST_URL, body=req.exceptions.Timeout())
    with req.Session() as s:
        with pytest.raises(df.NetworkTimeoutError):
            df._request_page(s, API_KEY, PAST_URL, {}, df.Stats())


@resp_lib.activate
def test_connection_error(env_key):
    import requests as req
    resp_lib.add(resp_lib.GET, PAST_URL, body=req.exceptions.ConnectionError())
    with req.Session() as s:
        with pytest.raises(df.NetworkConnectionError):
            df._request_page(s, API_KEY, PAST_URL, {}, df.Stats())


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------

def test_no_airport_shows_help(capsys):
    """No -a → prints help, returns 0."""
    rc = df.main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "usage:" in captured.out.lower() or "delay" in captured.out


def test_no_api_key(monkeypatch):
    monkeypatch.delenv("FLIGHTAWARE_API_KEY", raising=False)
    with patch("delayed_flights.load_dotenv"):
        rc = df.main(["-a", "WAW", "-p",
                      "--start", "2026-08-13T00:00:00Z",
                      "--end",   "2026-08-14T00:00:00Z"])
    assert rc == df.ConfigError.exit_code


def test_main_past_found(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    raw = _make_raw_flight("LO777",
                           scheduled_off="2026-08-13T10:00:00Z",
                           actual_off="2026-08-13T11:10:00Z")   # 70 min
    page = _page([raw], key="departures")

    with patch("delayed_flights._request_page", return_value=page):
        rc = df.main(["-a", "WAW", "-p",
                      "--start", "2026-08-13T00:00:00Z",
                      "--end",   "2026-08-14T00:00:00Z"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "LO777" in out
    assert "70 minutes" in out


def test_main_past_not_found(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    raw = _make_raw_flight("LO001",
                           scheduled_off="2026-08-13T10:00:00Z",
                           actual_off="2026-08-13T10:05:00Z")   # 5 min
    page = _page([raw], key="departures")

    with patch("delayed_flights._request_page", return_value=page):
        rc = df.main(["-a", "WAW", "-p",
                      "--start", "2026-08-13T00:00:00Z",
                      "--end",   "2026-08-14T00:00:00Z"])

    assert rc == 0
    assert "NO FLIGHT" in capsys.readouterr().out


def test_main_upcoming_found(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    raw = _make_raw_flight("LO888", actual_off=None, departure_delay=5400)  # 90 min
    page = _page([raw], key="scheduled_departures")

    with patch("delayed_flights._request_page", return_value=page):
        rc = df.main(["-a", "WAW"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "LO888" in out
    assert "90 minutes" in out


def test_main_json_past(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    raw = _make_raw_flight("LO999",
                           scheduled_off="2026-08-13T10:00:00Z",
                           actual_off="2026-08-13T11:30:00Z")   # 90 min
    page = _page([raw], key="departures")

    with patch("delayed_flights._request_page", return_value=page):
        rc = df.main(["-a", "WAW", "-p",
                      "--start", "2026-08-13T00:00:00Z",
                      "--end",   "2026-08-14T00:00:00Z",
                      "--json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == "0.0.1"
    assert data["mode"] == "past"
    assert data["flights"][0]["delay_minutes"] == 90


def test_main_json_upcoming(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    raw = _make_raw_flight("LO555", actual_off=None, departure_delay=3900)  # 65 min
    page = _page([raw], key="scheduled_departures")

    with patch("delayed_flights._request_page", return_value=page):
        rc = df.main(["-a", "WAW", "--json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "upcoming"
    assert data["flights"][0]["delay_minutes"] == 65


def test_main_all_flag(monkeypatch, capsys):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    flights = [
        _make_raw_flight("LO001", scheduled_off="2026-08-13T10:00:00Z",
                         actual_off="2026-08-13T11:05:00Z"),
        _make_raw_flight("LO002", scheduled_off="2026-08-13T10:00:00Z",
                         actual_off="2026-08-13T11:20:00Z"),
    ]
    page = _page(flights, key="departures")

    with patch("delayed_flights._request_page", return_value=page):
        rc = df.main(["-a", "WAW", "-p",
                      "--start", "2026-08-13T00:00:00Z",
                      "--end",   "2026-08-14T00:00:00Z",
                      "--all", "--json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["flights_found"] == 2


def test_start_end_without_past_raises(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    with patch("delayed_flights.load_dotenv"):
        rc = df.main(["-a", "WAW",
                      "--start", "2026-08-13T00:00:00Z",
                      "--end",   "2026-08-14T00:00:00Z"])
    assert rc == df.ValidationError.exit_code


def test_window_not_24h(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    with patch("delayed_flights.load_dotenv"):
        rc = df.main(["-a", "WAW", "-p",
                      "--start", "2026-08-13T00:00:00Z",
                      "--end",   "2026-08-13T12:00:00Z"])
    assert rc == df.ValidationError.exit_code


def test_invalid_date(monkeypatch):
    monkeypatch.setenv("FLIGHTAWARE_API_KEY", API_KEY)
    with patch("delayed_flights.load_dotenv"):
        rc = df.main(["-a", "WAW", "-p",
                      "--start", "not-a-date",
                      "--end",   "2026-08-14T00:00:00Z"])
    assert rc == df.ValidationError.exit_code


# ---------------------------------------------------------------------------
# Helpers / misc
# ---------------------------------------------------------------------------

def test_airport_label_iata():
    assert "Warsaw" in df.airport_label("WAW", None, None)

def test_airport_label_unknown():
    assert df.airport_label("ZZZ", None, None) == "ZZZ"

def test_airport_label_none():
    assert df.airport_label(None, None, None) == "Unknown"

def test_version_constant():
    assert df.VERSION == "0.0.1"

def test_module_attributes():
    for attr in ("main", "iter_past_delayed", "iter_upcoming_delayed",
                 "parse_iso8601", "validate_past_window",
                 "past_window", "upcoming_window", "fmt_local", "VERSION"):
        assert hasattr(df, attr), f"missing: {attr}"
