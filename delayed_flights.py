#!/usr/bin/env python3
"""
delayed_flights.py – FlightAware AeroAPI v4 CLI for finding delayed departures.

Endpoint used:  GET /airports/{id}/flights/departures
Delay criterion: actual_off - scheduled_off >= min_delay_minutes

Key API facts (from OpenAPI spec v4.30.0):
  - Parameters: start, end (ISO8601 datetime), max_pages, cursor
  - Response key: 'departures' (array), 'links.next' for pagination
  - Timing fields: scheduled_off / estimated_off / actual_off  (runway)
                   scheduled_out / actual_out                  (gate)
  - Auth header: x-apikey
  - Date range limit: within 10 days past and 2 days future
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://aeroapi.flightaware.com/aeroapi"
DEFAULT_AIRPORT = "GCTS"
DEFAULT_MIN_DELAY = 60        # minutes
REQUEST_TIMEOUT = 30          # seconds
MAX_HISTORY_DAYS = 10         # AeroAPI personal plan limit

# ---------------------------------------------------------------------------
# Airport city name lookup (ICAO → city/name)
# ---------------------------------------------------------------------------

AIRPORT_CITY: dict[str, str] = {
    "GCTS": "Tenerife South",
    "GCLA": "La Palma",
    "GCRR": "Lanzarote",
    "GCFV": "Fuerteventura",
    "GCLP": "Gran Canaria",
    "GCXO": "Tenerife North",
    "LEMD": "Madrid",
    "LEBL": "Barcelona",
    "LEAL": "Alicante",
    "EGLL": "London Heathrow",
    "EGKK": "London Gatwick",
    "EGSS": "London Stansted",
    "EGGW": "London Luton",
    "EHAM": "Amsterdam",
    "LFPG": "Paris CDG",
    "EDDF": "Frankfurt",
    "EDDM": "Munich",
    "LIRF": "Rome Fiumicino",
    "LOWW": "Vienna",
    "EPWA": "Warsaw",
    "EKCH": "Copenhagen",
    "ESSA": "Stockholm Arlanda",
    "ENGM": "Oslo",
    "EFHK": "Helsinki",
    "LTFM": "Istanbul",
    "OMDB": "Dubai",
    "KJFK": "New York JFK",
    "KLAX": "Los Angeles",
    "KORD": "Chicago O'Hare",
    "ZBAA": "Beijing",
    "VHHH": "Hong Kong",
    "YSSY": "Sydney",
}


def airport_label(
    code: Optional[str],
    name: Optional[str],
    city: Optional[str],
) -> str:
    """Return a human-readable label like 'EGSS (London Stansted)'."""
    if not code:
        return "Unknown"
    display = name or city or AIRPORT_CITY.get(code.upper())
    return f"{code} ({display})" if display else code


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DelayedFlight:
    ident: str
    ident_iata: Optional[str]
    ident_icao: Optional[str]
    origin_code: str
    origin_name: Optional[str]
    origin_city: Optional[str]
    destination_code: Optional[str]
    destination_name: Optional[str]
    destination_city: Optional[str]
    scheduled_off: datetime
    estimated_off: Optional[datetime]
    actual_off: datetime
    delay_minutes: int

    def _fmt(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ident": self.ident,
            "ident_iata": self.ident_iata,
            "ident_icao": self.ident_icao,
            "origin": airport_label(self.origin_code, self.origin_name, self.origin_city),
            "destination": airport_label(
                self.destination_code, self.destination_name, self.destination_city
            ),
            "scheduled_off": self._fmt(self.scheduled_off),
            "estimated_off": self._fmt(self.estimated_off),
            "actual_off": self._fmt(self.actual_off),
            "delay_minutes": self.delay_minutes,
        }

    def display(self) -> str:
        lines = ["FOUND DELAYED FLIGHT", ""]
        lines.append(f"  Flight:            {self.ident}")
        if self.ident_iata and self.ident_iata != self.ident:
            lines.append(f"  Flight (IATA):     {self.ident_iata}")
        if self.ident_icao and self.ident_icao != self.ident:
            lines.append(f"  Flight (ICAO):     {self.ident_icao}")
        lines.append(
            f"  Origin:            {airport_label(self.origin_code, self.origin_name, self.origin_city)}"
        )
        lines.append(
            f"  Destination:       {airport_label(self.destination_code, self.destination_name, self.destination_city)}"
        )
        lines.append(f"  Scheduled takeoff: {self._fmt(self.scheduled_off)}")
        if self.estimated_off:
            lines.append(f"  Estimated takeoff: {self._fmt(self.estimated_off)}")
        lines.append(f"  Actual takeoff:    {self._fmt(self.actual_off)}")
        lines.append(f"  Delay:             {self.delay_minutes} minutes")
        return "\n".join(lines)


@dataclass
class Stats:
    requests_made: int = 0
    flights_analyzed: int = 0
    flights_skipped: int = 0
    flights_found: int = 0

    def display(self) -> str:
        return "\n".join([
            "",
            "─" * 44,
            "STATISTICS",
            f"  HTTP requests made:    {self.requests_made}",
            f"  Flights analyzed:      {self.flights_analyzed}",
            f"  Skipped (missing ts):  {self.flights_skipped}",
            f"  Delayed flights found: {self.flights_found}",
        ])


# ---------------------------------------------------------------------------
# Typed error hierarchy
# ---------------------------------------------------------------------------

class AppError(Exception):
    exit_code: int = 1

class ConfigError(AppError):
    exit_code = 2

class ApiError(AppError):
    exit_code = 3

class AuthError(ApiError):
    exit_code = 4

class RateLimitError(ApiError):
    exit_code = 5

class ServerError(ApiError):
    exit_code = 6

class NetworkTimeoutError(ApiError):
    exit_code = 7

class NetworkConnectionError(ApiError):
    exit_code = 8

class ValidationError(AppError):
    exit_code = 9


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="delayed_flights.py",
        description="Find delayed departures from an airport using FlightAware AeroAPI v4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Use default airport (GCTS) with last completed 24-hour window
  python delayed_flights.py

  # Custom airport and window
  python delayed_flights.py --airport EPWA --start 2026-08-10T00:00:00Z --end 2026-08-11T00:00:00Z

  # All matching flights in JSON
  python delayed_flights.py --airport GCTS --min-delay 30 --all --json
        """,
    )
    parser.add_argument(
        "--airport", default=DEFAULT_AIRPORT, metavar="ICAO",
        help=f"Airport ICAO code (default: {DEFAULT_AIRPORT})",
    )
    parser.add_argument(
        "--start", metavar="ISO8601",
        help="Start of time window (inclusive). Must include timezone.",
    )
    parser.add_argument(
        "--end", metavar="ISO8601",
        help="End of time window (exclusive). Must include timezone.",
    )
    parser.add_argument(
        "--min-delay", type=int, default=DEFAULT_MIN_DELAY, metavar="MINUTES",
        help=f"Minimum delay in minutes (default: {DEFAULT_MIN_DELAY})",
    )
    parser.add_argument(
        "--all", action="store_true", dest="show_all",
        help="Return all matching flights instead of stopping after the first.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="output_json",
        help="Output results as JSON.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Date/time helpers
# ---------------------------------------------------------------------------

def parse_iso8601(s: str) -> datetime:
    """Parse an ISO 8601 datetime that MUST contain timezone info."""
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


def default_window() -> tuple[datetime, datetime]:
    """Return the last completed 24-h period ending 24 h ago."""
    now = datetime.now(tz=timezone.utc)
    end = now - timedelta(hours=24)
    start = end - timedelta(hours=24)
    return start, end


def validate_window(start: datetime, end: datetime) -> None:
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
        raise ValidationError(
            "End date is more than 2 days in the future, outside AeroAPI range."
        )


# ---------------------------------------------------------------------------
# HTTP / API layer
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    key = os.environ.get("FLIGHTAWARE_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "FLIGHTAWARE_API_KEY environment variable is not set or empty.\n"
            "Get a key at https://www.flightaware.com/commercial/aeroapi/\n"
            "Then: export FLIGHTAWARE_API_KEY=your_key_here\n"
            "Or create a .env file (see .env.example)."
        )
    return key


def _make_headers(api_key: str) -> dict[str, str]:
    return {"x-apikey": api_key, "Accept": "application/json; charset=UTF-8"}


def _parse_error_body(response: requests.Response) -> str:
    try:
        body = response.json()
        return body.get("detail") or body.get("title") or str(body)
    except ValueError:
        return response.text[:300]


def _request_page(
    session: requests.Session,
    api_key: str,
    airport: str,
    start: datetime,
    end: datetime,
    cursor: Optional[str],
    stats: Stats,
) -> dict[str, Any]:
    """Fetch one departures page with a single automatic retry on 429."""
    params: dict[str, Any] = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_pages": 1,
    }
    if cursor:
        params["cursor"] = cursor

    url = f"{BASE_URL}/airports/{airport}/flights/departures"

    for attempt in range(2):
        try:
            resp = session.get(url, headers=_make_headers(api_key), params=params,
                               timeout=REQUEST_TIMEOUT)
        except requests.exceptions.Timeout as exc:
            raise NetworkTimeoutError(
                f"Request timed out after {REQUEST_TIMEOUT} s. "
                "Check your connection or try again later."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise NetworkConnectionError(f"Network error: {exc}") from exc

        stats.requests_made += 1

        if resp.status_code == 429 and attempt == 0:
            try:
                wait = min(int(resp.headers.get("Retry-After", 5)), 60)
            except ValueError:
                wait = 5
            print(f"  [rate limit] Waiting {wait} s before retry…", file=sys.stderr)
            time.sleep(wait)
            continue

        # Success
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise ApiError(f"Non-JSON response from AeroAPI: {exc}") from exc

        # Error responses
        detail = _parse_error_body(resp)
        code = resp.status_code
        if code == 400:
            raise ApiError(f"HTTP 400 Bad Request – {detail}")
        if code in (401, 403):
            raise AuthError(
                f"HTTP {code} Unauthorized/Forbidden – {detail}\n"
                "Verify your FLIGHTAWARE_API_KEY."
            )
        if code == 429:
            raise RateLimitError(
                "HTTP 429 Rate Limited after retry. Slow down or upgrade your plan."
            )
        if 500 <= code < 600:
            raise ServerError(f"HTTP {code} Server Error – {detail}")
        raise ApiError(f"HTTP {code} – {detail}")

    # Unreachable, but satisfies type checker
    raise RateLimitError("Rate limit persists after retry.")


# ---------------------------------------------------------------------------
# Core iteration logic
# ---------------------------------------------------------------------------

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return parse_iso8601(value)
    except (ValidationError, ValueError):
        return None


def iter_delayed_flights(
    session: requests.Session,
    api_key: str,
    airport: str,
    start: datetime,
    end: datetime,
    min_delay: int,
    stop_at_first: bool,
    stats: Stats,
) -> Iterator[DelayedFlight]:
    """Paginate through AeroAPI and yield DelayedFlight objects."""
    cursor: Optional[str] = None

    while True:
        data = _request_page(session, api_key, airport, start, end, cursor, stats)

        departures = data.get("departures", [])
        if not isinstance(departures, list):
            raise ApiError("'departures' field is not a list in AeroAPI response.")

        for flight in departures:
            stats.flights_analyzed += 1

            scheduled_off = _parse_dt(flight.get("scheduled_off"))
            actual_off    = _parse_dt(flight.get("actual_off"))
            estimated_off = _parse_dt(flight.get("estimated_off"))

            if scheduled_off is None or actual_off is None:
                stats.flights_skipped += 1
                continue

            delay_minutes = int((actual_off - scheduled_off).total_seconds() / 60)
            if delay_minutes < min_delay:
                continue

            origin      = flight.get("origin") or {}
            destination = flight.get("destination") or {}

            df = DelayedFlight(
                ident              = flight.get("ident", "Unknown"),
                ident_iata         = flight.get("ident_iata"),
                ident_icao         = flight.get("ident_icao"),
                origin_code        = origin.get("code") or origin.get("code_icao") or airport,
                origin_name        = origin.get("name"),
                origin_city        = origin.get("city"),
                destination_code   = destination.get("code") or destination.get("code_icao"),
                destination_name   = destination.get("name"),
                destination_city   = destination.get("city"),
                scheduled_off      = scheduled_off,
                estimated_off      = estimated_off,
                actual_off         = actual_off,
                delay_minutes      = delay_minutes,
            )
            stats.flights_found += 1
            yield df

            if stop_at_first:
                return

        # Follow pagination cursor
        links = data.get("links") or {}
        next_url = links.get("next") if isinstance(links, dict) else None
        if next_url:
            qs     = parse_qs(urlparse(next_url).query)
            cursor = (qs.get("cursor") or [None])[0]
            if cursor:
                continue

        break  # No more pages


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_header(airport: str, start: datetime, end: datetime, min_delay: int) -> None:
    city  = AIRPORT_CITY.get(airport.upper(), "")
    label = f"{airport} ({city})" if city else airport
    print(f"\nSearching departures from {label}")
    print(f"  Window: {start.strftime('%Y-%m-%d %H:%M UTC')} → {end.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Min delay: {min_delay} minutes\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    load_dotenv()

    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1

    try:
        api_key = _get_api_key()

        # Resolve time window
        if args.start or args.end:
            if not (args.start and args.end):
                raise ValidationError("Both --start and --end must be provided together.")
            start = parse_iso8601(args.start)
            end   = parse_iso8601(args.end)
        else:
            start, end = default_window()
            print(
                f"No --start/--end given. Using window: "
                f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')} → {end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                file=sys.stderr,
            )

        validate_window(start, end)

        airport    = args.airport.strip().upper()
        min_delay  = args.min_delay
        stop_first = not args.show_all

        if not args.output_json:
            print_header(airport, start, end, min_delay)

        stats        : Stats              = Stats()
        found_flights: list[DelayedFlight] = []

        with requests.Session() as session:
            for flight in iter_delayed_flights(
                session, api_key, airport, start, end, min_delay, stop_first, stats
            ):
                found_flights.append(flight)
                if not args.output_json:
                    print(flight.display())
                    print()
                if stop_first:
                    break

        if args.output_json:
            result = {
                "airport": airport,
                "window_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_end"  : end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "min_delay_minutes": min_delay,
                "flights_found": stats.flights_found,
                "delayed_flights": [f.to_dict() for f in found_flights],
                "stats": {
                    "requests_made"   : stats.requests_made,
                    "flights_analyzed": stats.flights_analyzed,
                    "flights_skipped" : stats.flights_skipped,
                },
            }
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            if not found_flights:
                print(f"NO FLIGHT WITH DELAY >= {min_delay} MINUTES FOUND")
            print(stats.display())

        return 0

    except AppError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
