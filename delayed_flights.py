#!/usr/bin/env python3
"""
delayed_flights.py – FlightAware AeroAPI v4 CLI for finding delayed departures.

VERSION 0.0.1

Endpoints used (from OpenAPI spec v4.30.0):
  GET /airports/{id}/flights/scheduled_departures   ← default / daemon: upcoming planned delays
  GET /airports/{id}/flights/departures             ← --past: last 24 h, actual delays

Features:
  - Upcoming mode: scans future departures (scheduled_off >= now)
  - Past mode (-p): scans departures in the last 24 h
  - Daemon mode (-d): continuously checks every N minutes (default: 30m) for delays in next W hours (default: 6h)
  - Telegram integration: sends alerts to Telegram bot when configured via .env / CLI
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

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    ZoneInfo = None                      # type: ignore[assignment,misc]
    ZoneInfoNotFoundError = Exception    # type: ignore[assignment,misc]

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION = "0.0.1"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL          = "https://aeroapi.flightaware.com/aeroapi"
DEFAULT_MIN_DELAY = 60      # minutes
REQUEST_TIMEOUT   = 30      # seconds
MAX_HISTORY_DAYS  = 10      # AeroAPI personal plan limit
DEFAULT_FUTURE_H  = 6       # hours ahead for upcoming / daemon mode
DEFAULT_INTERVAL  = 30      # minutes between checks in daemon mode

# ---------------------------------------------------------------------------
# Dynamic & Persistent Airport Cache (retrieved from AeroAPI and cached on disk)
# ---------------------------------------------------------------------------

AIRPORT_CACHE_FILE = os.environ.get(
    "DELAY_AIRPORT_CACHE_FILE",
    os.path.expanduser("~/.cache/delayed_flights/airports.json"),
)

AIRPORT_CACHE: dict[str, str] = {}


def load_airport_cache(filepath: str = AIRPORT_CACHE_FILE) -> dict[str, str]:
    """Load persistent airport cache from disk into AIRPORT_CACHE."""
    global AIRPORT_CACHE
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    AIRPORT_CACHE.update(data)
        except Exception:
            pass
    return AIRPORT_CACHE


def save_airport_cache(filepath: str = AIRPORT_CACHE_FILE) -> None:
    """Save in-memory AIRPORT_CACHE to disk to avoid repeated API lookups."""
    if not AIRPORT_CACHE:
        return
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(AIRPORT_CACHE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def cache_airport(code: Optional[str], name: Optional[str] = None, city: Optional[str] = None, persist: bool = True) -> None:
    """Cache airport display label dynamically from API data and save to disk."""
    if not code:
        return
    code_upper = code.strip().upper()
    display = name or city
    if display and AIRPORT_CACHE.get(code_upper) != display:
        AIRPORT_CACHE[code_upper] = display
        if persist:
            save_airport_cache()


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
                save_airport_cache()
            return data
    except Exception:
        pass
    return {}


def airport_label(code: Optional[str], name: Optional[str] = None, city: Optional[str] = None) -> str:
    """Return 'CODE (Airport Name / City)' or just 'CODE' using dynamic & cached AeroAPI data."""
    if not code:
        return "Unknown"
    code_upper = code.strip().upper()
    display = name or city or AIRPORT_CACHE.get(code_upper)
    if display:
        cache_airport(code_upper, display)
        return f"{code} ({display})"
    return code


def fmt_local(dt: Optional[datetime], tz_name: Optional[str]) -> str:
    """
    Format UTC datetime as '2026-08-13 14:35 UTC  (15:35 WEST)'.
    Falls back to UTC-only if tz_name is unknown or zoneinfo unavailable.
    """
    if dt is None:
        return "N/A"
    utc_str = dt.strftime("%Y-%m-%d %H:%M UTC")
    if tz_name and ZoneInfo is not None:
        try:
            local_dt = dt.astimezone(ZoneInfo(tz_name))
            return f"{utc_str}  ({local_dt.strftime('%H:%M')} {local_dt.strftime('%Z')})"
        except (ZoneInfoNotFoundError, Exception):
            pass
    return utc_str
    if tz_name and ZoneInfo is not None:
        try:
            local_dt = dt.astimezone(ZoneInfo(tz_name))
            return f"{utc_str}  ({local_dt.strftime('%H:%M')} {local_dt.strftime('%Z')})"
        except (ZoneInfoNotFoundError, Exception):
            pass
    return utc_str


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Flight:
    """Represents one flight with a planned or actual delay."""
    ident: str
    ident_iata: Optional[str]
    ident_icao: Optional[str]
    origin_code: str
    origin_name: Optional[str]
    origin_city: Optional[str]
    origin_tz: Optional[str]
    destination_code: Optional[str]
    destination_name: Optional[str]
    destination_city: Optional[str]
    destination_tz: Optional[str]
    scheduled_off: Optional[datetime]
    estimated_off: Optional[datetime]
    actual_off: Optional[datetime]
    delay_minutes: int
    is_past: bool          # True = actual departure, False = future/scheduled

    def _fmt(self, dt: Optional[datetime]) -> str:
        return fmt_local(dt, self.origin_tz)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ident":                self.ident,
            "ident_iata":           self.ident_iata,
            "ident_icao":           self.ident_icao,
            "origin":               airport_label(self.origin_code, self.origin_name, self.origin_city),
            "origin_timezone":      self.origin_tz,
            "destination":          airport_label(self.destination_code, self.destination_name, self.destination_city),
            "destination_timezone": self.destination_tz,
            "scheduled_off":        self._fmt(self.scheduled_off),
            "estimated_off":        self._fmt(self.estimated_off) if self.estimated_off else None,
            "actual_off":           self._fmt(self.actual_off)    if self.actual_off    else None,
            "delay_minutes":        self.delay_minutes,
            "mode":                 "past" if self.is_past else "upcoming",
        }

    def display(self, label: str = "DELAYED FLIGHT") -> str:
        lines = [label, ""]
        lines.append(f"  Flight:            {self.ident}")
        if self.ident_iata and self.ident_iata != self.ident:
            lines.append(f"  Flight (IATA):     {self.ident_iata}")
        if self.ident_icao and self.ident_icao != self.ident:
            lines.append(f"  Flight (ICAO):     {self.ident_icao}")
        lines.append(f"  Origin:            {airport_label(self.origin_code, self.origin_name, self.origin_city)}")
        lines.append(f"  Destination:       {airport_label(self.destination_code, self.destination_name, self.destination_city)}")
        if self.scheduled_off:
            lines.append(f"  Scheduled takeoff: {self._fmt(self.scheduled_off)}")
        if self.estimated_off:
            lines.append(f"  Estimated takeoff: {self._fmt(self.estimated_off)}")
        if self.actual_off:
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
            f"  Skipped (no delay):    {self.flights_skipped}",
            f"  Delayed flights found: {self.flights_found}",
        ])


# ---------------------------------------------------------------------------
# Telegram integration
# ---------------------------------------------------------------------------

def get_telegram_config() -> tuple[Optional[str], Optional[str]]:
    """Retrieve Telegram bot token and chat ID from environment if configured."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
    return bot_token, chat_id


def format_telegram_flight(flight: Flight) -> str:
    """Format flight details into a rich HTML Telegram message."""
    origin_lbl = airport_label(flight.origin_code, flight.origin_name, flight.origin_city)
    dest_lbl   = airport_label(flight.destination_code, flight.destination_name, flight.destination_city)

    title = "⚠️ <b>OPÓŹNIONY ODLOT</b>" if flight.is_past else "⏳ <b>PLANOWANE OPÓŹNIENIE ODLOTU</b>"

    lines = [
        title,
        "",
        f"✈️ <b>Lot:</b> <code>{flight.ident}</code>",
    ]
    if flight.ident_iata and flight.ident_iata != flight.ident:
        lines.append(f"🏷️ <b>IATA:</b> {flight.ident_iata}")
    lines.append(f"🛫 <b>Skąd:</b> {origin_lbl}")
    lines.append(f"🛬 <b>Dokąd:</b> {dest_lbl}")
    if flight.scheduled_off:
        lines.append(f"🕒 <b>Planowy start:</b> {flight._fmt(flight.scheduled_off)}")
    if flight.estimated_off:
        lines.append(f"⏱️ <b>Szacowany start:</b> {flight._fmt(flight.estimated_off)}")
    if flight.actual_off:
        lines.append(f"🚀 <b>Faktyczny start:</b> {flight._fmt(flight.actual_off)}")
    lines.append(f"🚨 <b>Opóźnienie:</b> <b>+{flight.delay_minutes} min</b>")
    return "\n".join(lines)


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    session: Optional[requests.Session] = None,
    parse_mode: str = "HTML",
) -> bool:
    """Send a message to Telegram via Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    s = session or requests.Session()
    try:
        resp = s.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return True
        print(f"Telegram API error ({resp.status_code}): {resp.text}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Telegram connection error: {exc}", file=sys.stderr)
        return False


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


def parse_duration_to_seconds(val: Optional[str | float]) -> Optional[int]:
    """
    Parse a duration string into total seconds.
    Supported formats: '4', '4.5', '4h', '30m', '1d', '7200s'.
    Default unit without suffix is hours.
    """
    if val is None:
        return None
    s = str(val).strip().lower()
    if not s:
        return None
    try:
        if s.endswith("m"):
            return int(float(s[:-1]) * 60)
        if s.endswith("h"):
            return int(float(s[:-1]) * 3600)
        if s.endswith("d"):
            return int(float(s[:-1]) * 86400)
        if s.endswith("s"):
            return int(float(s[:-1]))
        return int(float(s) * 3600)
    except ValueError as exc:
        raise ValidationError(
            f"Invalid duration format '{val}'. Use e.g. '4', '4h', '30m', '1d'."
        ) from exc


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delay",
        description=f"delayed_flights v{VERSION} – FlightAware AeroAPI v4 departure delay finder & Telegram notifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
modes:
  (default)  Upcoming departures in the next N hours (default: 6h) with planned delay.
             Uses: GET /airports/{id}/flights/scheduled_departures
  -p/--past  Actual delayed departures in the last 24h.
             Uses: GET /airports/{id}/flights/departures
  -d/--daemon Run continuously in monitoring mode, checking every N minutes and alerting to Telegram.

examples:
  delay -a WAW                          # planned delays in next 6h
  delay -a WAW -w 9                     # planned delays in next 9h
  delay -a WAW -d                       # daemon mode: check WAW every 30m indefinitely
  delay -a WAW -d -D 4h                 # daemon mode: run for 4 hours then exit
  delay -a WAW -d -w 6 -i 15 -D 8h      # check every 15m for 8 hours total
  delay -a WAW -p                       # actual delays, last 24h
  delay -a LPA -p --all                 # all actual delayed, last 24h
  delay -a STN --min-delay 30           # planned delay >= 30 min
  delay -a TFS -p --start 2026-08-10T00:00:00Z --end 2026-08-11T00:00:00Z
  delay -a WAW -t                       # send single run results to Telegram (-t)
  delay -a WAW --json                   # JSON output
        """,
    )
    parser.add_argument(
        "-a", "--airport", metavar="IATA/ICAO", default=None,
        help="Airport IATA or ICAO code (required). If omitted, shows this help.",
    )
    parser.add_argument(
        "-p", "--past", action="store_true", dest="past",
        help="Past mode: show actual delayed departures in the last 24 h.",
    )
    parser.add_argument(
        "-d", "--daemon", action="store_true", dest="daemon",
        help="Daemon mode: monitor airport periodically and send alerts to Telegram.",
    )
    parser.add_argument(
        "-D", "--duration", "--runtime", metavar="DURATION", dest="duration", default=None,
        help="How long the daemon should run (e.g. '4', '4h', '30m', '1d'). Default: run indefinitely.",
    )
    parser.add_argument(
        "-w", "--hours", "--window", type=int, default=DEFAULT_FUTURE_H, metavar="HOURS", dest="hours",
        help=f"Future window in hours for upcoming flights (default: {DEFAULT_FUTURE_H}h).",
    )
    parser.add_argument(
        "-i", "--interval", type=int, default=DEFAULT_INTERVAL, metavar="MINUTES", dest="interval",
        help=f"Daemon check interval in minutes (default: {DEFAULT_INTERVAL}m).",
    )
    parser.add_argument(
        "-t", "--telegram", action="store_true", dest="telegram",
        help="Send delayed flights alerts to Telegram (requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID).",
    )
    parser.add_argument(
        "--start", metavar="ISO8601",
        help="Custom start of window (inclusive, with timezone). Only with -p.",
    )
    parser.add_argument(
        "--end", metavar="ISO8601",
        help="Custom end of window (exclusive, with timezone). Only with -p.",
    )
    parser.add_argument(
        "--min-delay", type=int, default=DEFAULT_MIN_DELAY, metavar="MINUTES",
        help=f"Minimum delay in minutes (default: {DEFAULT_MIN_DELAY}).",
    )
    parser.add_argument(
        "--all", action="store_true", dest="show_all",
        help="Show all matching flights (default: stop after first in one-shot mode).",
    )
    parser.add_argument(
        "--json", action="store_true", dest="output_json",
        help="Output results as JSON.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}",
    )
    return parser


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Date/time helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# HTTP / API layer
# ---------------------------------------------------------------------------

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
            resp = session.get(url, headers=_make_headers(api_key),
                               params=params, timeout=REQUEST_TIMEOUT)
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


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

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

    origin_code = origin.get("code") or origin.get("code_icao") or airport
    origin_name = origin.get("name")
    origin_city = origin.get("city")
    if origin_code:
        cache_airport(origin_code, origin_name, origin_city)

    dest_code = destination.get("code") or destination.get("code_icao")
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


# ---------------------------------------------------------------------------
# Main iterators
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Daemon / Monitoring mode
# ---------------------------------------------------------------------------

def run_daemon_loop(
    session: requests.Session,
    api_key: str,
    airport: str,
    hours: int,
    interval_minutes: int,
    min_delay: int,
    bot_token: Optional[str],
    chat_id: Optional[str],
    duration_seconds: Optional[int] = None,
) -> None:
    """Continuous monitor loop checking every `interval_minutes` for delayed flights."""
    fetch_airport_info(session, api_key, airport)
    label = airport_label(airport)

    daemon_start = datetime.now(tz=timezone.utc)
    deadline     = daemon_start + timedelta(seconds=duration_seconds) if duration_seconds else None

    print(f"\n🚀 [DAEMON] Uruchomiono monitorowanie opóźnień dla {label}")
    print(f"  Okno przyszłości:     +{hours} h")
    print(f"  Częstotliwość:        co {interval_minutes} min")
    print(f"  Minimalne opóźnienie: >= {min_delay} min")
    if duration_seconds:
        dur_h = duration_seconds / 3600
        print(f"  Czas działania:       {dur_h:.2f} h (do {deadline.strftime('%Y-%m-%d %H:%M:%S UTC') if deadline else ''})")
    else:
        print("  Czas działania:       Bez limitu (do zatrzymania Ctrl+C)")

    if bot_token and chat_id:
        print(f"  Powiadomienia:        Telegram (chat_id: {chat_id})")
    else:
        print("  Powiadomienia:        Tylko konsola (brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    print("  Naciśnij Ctrl+C aby zatrzymać monitorowanie.\n")

    notified_flights: set[str] = set()

    while True:
        cycle_start = datetime.now(tz=timezone.utc)
        if deadline and cycle_start >= deadline:
            print(f"\n⏰ [DAEMON] Osiągnięto limit czasu działania ({duration_seconds/3600:.2f} h). Zakończono monitorowanie.")
            break

        start = cycle_start
        end   = cycle_start + timedelta(hours=hours)
        ts_str = cycle_start.strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[{ts_str}] Sprawdzam odloty {airport} w oknie {start.strftime('%H:%M')} – {end.strftime('%H:%M UTC')}…")

        stats = Stats()
        try:
            for flight in iter_upcoming_delayed(
                session, api_key, airport, start, end, min_delay, stop_at_first=False, stats=stats
            ):
                flight_key = f"{flight.ident}_{flight.scheduled_off.isoformat() if flight.scheduled_off else ''}_{flight.delay_minutes}"
                if flight_key not in notified_flights:
                    notified_flights.add(flight_key)
                    print(f"\n🚨 NOWE OPÓŹNIENIE ZNALEZIONE: {flight.ident} (+{flight.delay_minutes} min)")
                    print(flight.display("UPCOMING DELAYED FLIGHT"))
                    print()

                    if bot_token and chat_id:
                        msg = format_telegram_flight(flight)
                        success = send_telegram_message(bot_token, chat_id, msg, session=session)
                        if success:
                            print(f"  ✓ Wysłano powiadomienie Telegram dla {flight.ident}")
                        else:
                            print(f"  ✗ Błąd wysyłania powiadomienia Telegram dla {flight.ident}", file=sys.stderr)

            print(f"  Przeanalizowano {stats.flights_analyzed} lotów, znaleziono opóźnionych: {stats.flights_found}.")

        except AppError as err:
            print(f"  [BŁĄD API w cyklu]: {err}", file=sys.stderr)
        except Exception as exc:
            print(f"  [BŁĄD w cyklu]: {exc}", file=sys.stderr)

        now_after = datetime.now(tz=timezone.utc)
        if deadline and now_after >= deadline:
            print(f"\n⏰ [DAEMON] Osiągnięto limit czasu działania ({duration_seconds/3600:.2f} h). Zakończono monitorowanie.")
            break

        # Calculate sleep seconds (don't sleep past deadline)
        sleep_seconds = interval_minutes * 60
        if deadline:
            sec_left = int((deadline - now_after).total_seconds())
            if sec_left <= 0:
                print(f"\n⏰ [DAEMON] Osiągnięto limit czasu działania ({duration_seconds/3600:.2f} h). Zakończono monitorowanie.")
                break
            sleep_seconds = min(sleep_seconds, sec_left)

        mins = sleep_seconds // 60
        secs = sleep_seconds % 60
        time_msg = f"{mins} min" if secs == 0 else f"{mins} min {secs} s"
        print(f"  Kolejne sprawdzenie za {time_msg}…\n")

        for _ in range(sleep_seconds):
            time.sleep(1)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_header(airport: str, start: datetime, end: datetime,
                 min_delay: int, mode: str) -> None:
    label = airport_label(airport)
    print(f"\n{label}  –  {mode}")
    print(f"  Window:    {start.strftime('%Y-%m-%d %H:%M UTC')} → {end.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Min delay: {min_delay} min\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    load_dotenv()
    load_airport_cache()

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1

    # Show help when no airport given
    if not args.airport:
        parser.print_help()
        return 0

    try:
        api_key   = _get_api_key()
        airport   = args.airport.strip().upper()
        min_delay = args.min_delay
        hours     = max(args.hours, 1)
        interval  = max(args.interval, 1)
        duration_sec = parse_duration_to_seconds(args.duration)

        bot_token, chat_id = get_telegram_config()

        # ── DAEMON MODE ───────────────────────────────────────────────────
        if args.daemon:
            if args.past:
                raise ValidationError("Cannot combine --daemon with --past.")
            if args.start or args.end:
                raise ValidationError("Cannot combine --daemon with --start/--end.")

            with requests.Session() as session:
                run_daemon_loop(
                    session=session,
                    api_key=api_key,
                    airport=airport,
                    hours=hours,
                    interval_minutes=interval,
                    min_delay=min_delay,
                    bot_token=bot_token,
                    chat_id=chat_id,
                    duration_seconds=duration_sec,
                )
            return 0

        # ── PAST MODE ─────────────────────────────────────────────────────
        if args.past:
            if args.start or args.end:
                if not (args.start and args.end):
                    raise ValidationError("Both --start and --end must be provided together.")
                start = parse_iso8601(args.start)
                end   = parse_iso8601(args.end)
                validate_past_window(start, end)
            else:
                start, end = past_window()
                print(
                    f"--past: {start.strftime('%Y-%m-%dT%H:%M:%SZ')} → "
                    f"{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                    file=sys.stderr,
                )

            mode_label  = "PAST – actual delayed departures (last 24 h)"
            mode_key    = "past"
            iterator_fn = iter_past_delayed
            stop_first  = not args.show_all

        # ── UPCOMING ONE-SHOT MODE ────────────────────────────────────────
        else:
            if args.start or args.end:
                raise ValidationError(
                    "--start/--end are only supported with -p/--past. "
                    f"Upcoming mode always uses the next {hours} hours."
                )
            start, end = upcoming_window(hours=hours)
            print(
                f"Upcoming: {start.strftime('%Y-%m-%dT%H:%M:%SZ')} → "
                f"{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                file=sys.stderr,
            )

            mode_label  = f"UPCOMING – planned delayed departures (next {hours} h)"
            mode_key    = "upcoming"
            iterator_fn = iter_upcoming_delayed
            stop_first  = not args.show_all

        stats         = Stats()
        found_flights: list[Flight] = []

        with requests.Session() as session:
            fetch_airport_info(session, api_key, airport)
            if not args.output_json:
                print_header(airport, start, end, min_delay, mode_label)

            for flight in iterator_fn(
                session, api_key, airport, start, end, min_delay, stop_first, stats
            ):
                found_flights.append(flight)
                if not args.output_json:
                    label = "FOUND DELAYED FLIGHT" if args.past else "UPCOMING DELAYED FLIGHT"
                    print(flight.display(label))
                    print()

                # Optional Telegram dispatch in one-shot mode
                if args.telegram:
                    if not (bot_token and chat_id):
                        raise ConfigError(
                            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env to use --telegram."
                        )
                    send_telegram_message(bot_token, chat_id, format_telegram_flight(flight), session=session)

                if stop_first:
                    break

        if args.output_json:
            result = {
                "version":           VERSION,
                "airport":           airport,
                "mode":              mode_key,
                "window_start":      start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_end":        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "min_delay_minutes": min_delay,
                "flights_found":     stats.flights_found,
                "flights":           [f.to_dict() for f in found_flights],
                "stats": {
                    "requests_made":    stats.requests_made,
                    "flights_analyzed": stats.flights_analyzed,
                    "flights_skipped":  stats.flights_skipped,
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
        print("\nZatrzymano monitorowanie (Ctrl+C).", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
