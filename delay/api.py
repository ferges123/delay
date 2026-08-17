"""FlightAware AeroAPI v4 client and flight departure iterators."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional
from urllib.parse import parse_qs, urlparse

import requests

from delay.cache import AIRPORT_CACHE, cache_airport, save_airport_cache
from delay.config import (
    BASE_URL,
    DEFAULT_FUTURE_H,
    MAX_HISTORY_DAYS,
    REQUEST_TIMEOUT,
)
from delay.exceptions import (
    ApiError,
    AuthError,
    ConfigError,
    NetworkConnectionError,
    NetworkTimeoutError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from delay.models import Flight, Stats


def _get_api_key() -> str:
    key = os.environ.get("FLIGHTAWARE_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "FLIGHTAWARE_API_KEY is not set.\n"
            "Get a key at https://www.flightaware.com/commercial/aeroapi/\n"
            "Add it to /opt/delay/.env:  FLIGHTAWARE_API_KEY=your_key_here"
        )
    return key


def _make_headers(api_key: str) -> dict[str, str]:
    return {"x-apikey": api_key, "Accept": "application/json; charset=UTF-8"}


def _parse_error_body(resp: requests.Response) -> str:
    try:
        b = resp.json()
        return b.get("detail") or b.get("title") or str(b)
    except ValueError:
        return resp.text[:300]


def _request_page(
    session: requests.Session,
    api_key: str,
    url: str,
    params: dict[str, Any],
    stats: Stats,
) -> dict[str, Any]:
    """GET one page; one gentle 429 retry respecting Retry-After."""
    for attempt in range(2):
        try:
            resp = session.get(
                url,
                headers=_make_headers(api_key),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout as exc:
            raise NetworkTimeoutError(
                f"Request timed out after {REQUEST_TIMEOUT} s."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise NetworkConnectionError(f"Network error: {exc}") from exc

        stats.requests_made += 1

        if resp.status_code == 429 and attempt == 0:
            try:
                wait = min(int(resp.headers.get("Retry-After", 5)), 60)
            except ValueError:
                wait = 5
            print(f"  [rate limit] Waiting {wait} s…", file=sys.stderr)
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise ApiError(f"Non-JSON response: {exc}") from exc

        detail = _parse_error_body(resp)
        code   = resp.status_code
        if code == 400:
            raise ApiError(f"HTTP 400 Bad Request – {detail}")
        if code in (401, 403):
            raise AuthError(f"HTTP {code} Unauthorized – {detail}")
        if code == 429:
            raise RateLimitError("HTTP 429 Rate Limited after retry.")
        if 500 <= code < 600:
            raise ServerError(f"HTTP {code} Server Error – {detail}")
        raise ApiError(f"HTTP {code} – {detail}")

    raise RateLimitError("Rate limit persists after retry.")


def parse_iso8601(s: str) -> datetime:
    s_norm = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s_norm)
    except ValueError as exc:
        raise ValidationError(
            f"Cannot parse date '{s}': {exc}. "
            "Use ISO 8601 with timezone, e.g. 2026-08-10T00:00:00Z"
        ) from exc
    if dt.tzinfo is None:
        raise ValidationError(
            f"Date '{s}' has no timezone. All dates must include timezone info."
        )
    return dt.astimezone(timezone.utc)


def past_window() -> tuple[datetime, datetime]:
    """Last 24 hours up to now."""
    now = datetime.now(tz=timezone.utc)
    return now - timedelta(hours=24), now


def upcoming_window(hours: int = DEFAULT_FUTURE_H) -> tuple[datetime, datetime]:
    """Now to now + hours."""
    now = datetime.now(tz=timezone.utc)
    return now, now + timedelta(hours=hours)


def validate_past_window(start: datetime, end: datetime) -> None:
    delta = end - start
    if delta != timedelta(hours=24):
        raise ValidationError(
            f"Time window must be exactly 24 hours. Got {delta.total_seconds()/3600:.2f} h."
        )
    now = datetime.now(tz=timezone.utc)
    if start < now - timedelta(days=MAX_HISTORY_DAYS):
        raise ValidationError(
            f"Start date is more than {MAX_HISTORY_DAYS} days in the past. "
            "AeroAPI Personal plan supports at most 10 days of history."
        )
    if end > now + timedelta(days=2):
        raise ValidationError("End date is more than 2 days in the future.")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return parse_iso8601(value)
    except (ValidationError, ValueError):
        return None


def _build_flight(raw: dict, airport: str, is_past: bool) -> Optional[Flight]:
    """Parse a raw API flight dict into a Flight. Returns None if delay cannot be determined."""
    origin      = raw.get("origin") or {}
    destination = raw.get("destination") or {}

    scheduled_off = _parse_dt(raw.get("scheduled_off"))
    estimated_off = _parse_dt(raw.get("estimated_off"))
    actual_off    = _parse_dt(raw.get("actual_off"))

    if is_past:
        # Actual delay: actual_off − scheduled_off
        if scheduled_off is None or actual_off is None:
            return None
        delay_minutes = int((actual_off - scheduled_off).total_seconds() / 60)
    else:
        # Planned delay from API field departure_delay (seconds → minutes)
        dep_delay_sec = raw.get("departure_delay")
        if dep_delay_sec is None:
            return None
        delay_minutes = int(dep_delay_sec / 60)

    origin_code = origin.get("code_iata") or origin.get("code") or origin.get("code_icao") or airport
    origin_name = origin.get("name")
    origin_city = origin.get("city")
    if origin_code:
        cache_airport(origin_code, origin_name, origin_city)

    dest_code = destination.get("code_iata") or destination.get("code") or destination.get("code_icao")
    dest_name = destination.get("name")
    dest_city = destination.get("city")
    if dest_code:
        cache_airport(dest_code, dest_name, dest_city)

    return Flight(
        ident            = raw.get("ident", "Unknown"),
        ident_iata       = raw.get("ident_iata"),
        ident_icao       = raw.get("ident_icao"),
        origin_code      = origin_code,
        origin_name      = origin_name,
        origin_city      = origin_city,
        origin_tz        = origin.get("timezone"),
        destination_code = dest_code,
        destination_name = dest_name,
        destination_city = dest_city,
        destination_tz   = destination.get("timezone"),
        scheduled_off    = scheduled_off,
        estimated_off    = estimated_off,
        actual_off       = actual_off,
        delay_minutes    = delay_minutes,
        is_past          = is_past,
    )


def _next_cursor(data: dict) -> Optional[str]:
    links = data.get("links") or {}
    next_url = links.get("next") if isinstance(links, dict) else None
    if next_url:
        qs = parse_qs(urlparse(next_url).query)
        return (qs.get("cursor") or [None])[0]
    return None


def iter_past_delayed(
    session: requests.Session,
    api_key: str,
    airport: str,
    start: datetime,
    end: datetime,
    min_delay: int,
    stop_at_first: bool,
    stats: Stats,
) -> Iterator[Flight]:
    """Paginate GET /airports/{id}/flights/departures, yield actual delayed flights."""
    url    = f"{BASE_URL}/airports/{airport}/flights/departures"
    cursor: Optional[str] = None

    while True:
        params: dict[str, Any] = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "max_pages": 1,
        }
        if cursor:
            params["cursor"] = cursor

        data       = _request_page(session, api_key, url, params, stats)
        departures = data.get("departures", [])
        if not isinstance(departures, list):
            raise ApiError("'departures' is not a list in AeroAPI response.")

        for raw in departures:
            stats.flights_analyzed += 1
            flight = _build_flight(raw, airport, is_past=True)
            if flight is None:
                stats.flights_skipped += 1
                continue
            if flight.delay_minutes < min_delay:
                stats.flights_skipped += 1
                continue
            stats.flights_found += 1
            yield flight
            if stop_at_first:
                return

        cursor = _next_cursor(data)
        if not cursor:
            break


def iter_upcoming_delayed(
    session: requests.Session,
    api_key: str,
    airport: str,
    start: datetime,
    end: datetime,
    min_delay: int,
    stop_at_first: bool,
    stats: Stats,
) -> Iterator[Flight]:
    """
    Paginate GET /airports/{id}/flights/scheduled_departures, yield planned delayed flights.
    Only flights whose scheduled_off >= now are returned (haven't yet departed according to schedule).
    """
    url    = f"{BASE_URL}/airports/{airport}/flights/scheduled_departures"
    cursor: Optional[str] = None

    while True:
        now_utc = datetime.now(tz=timezone.utc)

        params: dict[str, Any] = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "max_pages": 1,
        }
        if cursor:
            params["cursor"] = cursor

        data       = _request_page(session, api_key, url, params, stats)
        departures = data.get("scheduled_departures", [])
        if not isinstance(departures, list):
            raise ApiError("'scheduled_departures' is not a list in AeroAPI response.")

        for raw in departures:
            stats.flights_analyzed += 1
            flight = _build_flight(raw, airport, is_past=False)
            if flight is None:
                stats.flights_skipped += 1
                continue

            # Core condition: scheduled takeoff must still be in the future
            if flight.scheduled_off is None or flight.scheduled_off < now_utc:
                stats.flights_skipped += 1
                continue

            if flight.delay_minutes < min_delay:
                stats.flights_skipped += 1
                continue

            stats.flights_found += 1
            yield flight
            if stop_at_first:
                return

        cursor = _next_cursor(data)
        if not cursor:
            break


def fetch_airport_info(
    session: requests.Session,
    api_key: str,
    airport_code: str,
) -> dict[str, Any]:
    """Fetch airport details from GET /airports/{id} and cache to disk if not present."""
    code_upper = airport_code.strip().upper()
    if code_upper in AIRPORT_CACHE:
        return {"name": AIRPORT_CACHE[code_upper]}

    url = f"{BASE_URL}/airports/{code_upper}"
    try:
        resp = session.get(url, headers=_make_headers(api_key), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            name = data.get("name")
            city = data.get("city")
            display = name or city
            if display:
                AIRPORT_CACHE[code_upper] = display
                if data.get("code_icao"):
                    AIRPORT_CACHE[data["code_icao"].upper()] = display
                if data.get("code_iata"):
                    AIRPORT_CACHE[data["code_iata"].upper()] = display
                if data.get("code_icao") and data.get("code_iata"):
                    AIRPORT_CACHE[f"IATA_FOR_{data['code_icao'].upper()}"] = data["code_iata"].upper()
                save_airport_cache()
            return data
    except Exception:
        pass
    return {}
