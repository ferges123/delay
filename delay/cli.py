"""Command-line interface argument parsing and main execution entrypoint."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from delay.api import (
    _get_api_key,
    fetch_airport_info,
    iter_past_delayed,
    iter_upcoming_delayed,
    parse_iso8601,
    past_window,
    upcoming_window,
    validate_past_window,
)
from delay.cache import airport_label, load_airport_cache
from delay.config import (
    DEFAULT_DAEMON_DURATION_H,
    DEFAULT_FUTURE_H,
    DEFAULT_INTERVAL,
    DEFAULT_MIN_DELAY,
    VERSION,
)
from delay.daemon import (
    handle_logs_daemon,
    handle_status_daemon,
    handle_stop_daemon,
    run_daemon_loop,
    spawn_background_daemon,
)
from delay.exceptions import AppError, ConfigError, ValidationError
from delay.history import append_to_history, display_history
from delay.models import Flight, Stats
from delay.telegram import (
    format_telegram_flight,
    get_telegram_config,
    send_telegram_message,
)


def parse_duration_to_seconds(val: Optional[str | float]) -> Optional[int]:
    """
    Parse a duration string into total seconds.
    Supported formats: '4', '4.5', '4h', '30m', '1d', '7200s', '0'/'unlimited'/'inf'.
    Default unit without suffix is hours.
    """
    if val is None:
        return None
    s = str(val).strip().lower()
    if not s or s in ("0", "none", "inf", "unlimited", "infinite"):
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
            f"Invalid duration format '{val}'. Use e.g. '4', '4h', '30m', '1d', 'unlimited'."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delay",
        description=f"delayed_flights v{VERSION} – FlightAware AeroAPI v4 departure delay finder & Telegram notifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
modes:
  (default)  Upcoming departures in the next N hours (default: {DEFAULT_FUTURE_H}h) with planned delay.
             Uses: GET /airports/{{id}}/flights/scheduled_departures
  -p/--past  Actual delayed departures in the last 24h.
             Uses: GET /airports/{{id}}/flights/departures
  -d/--daemon Run continuously in background monitoring mode, checking every N minutes and alerting to Telegram.

examples:
  delay -a WAW                          # planned delays in next 6h
  delay -a WAW -w 9                     # planned delays in next 9h
  delay -a WAW -d                       # run in background: check WAW every 30m for 4h (safe to close terminal)
  delay -a WAW -d -D 8h                 # run in background for 8 hours then exit
  delay --status                        # check background daemon status
  delay --logs                          # view background daemon logs
  delay --stop                          # stop background daemon
  delay -a WAW -d -f                    # run daemon in foreground (-f)
  delay -a WAW -d -w 6 -i 15 -D 8h      # check every 15m for 8 hours total
  delay -a WAW -p                       # actual delays, last 24h
  delay -a LPA -p --all                 # all actual delayed, last 24h
  delay -a STN --min-delay 30           # planned delay >= 30 min
  delay -a TFS -p --start 2026-08-10T00:00:00Z --end 2026-08-11T00:00:00Z
  delay -a WAW -t                       # send single run results to Telegram (-t)
  delay -a WAW --json                   # JSON output
  delay --history                       # show recent delay history from CSV log
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
        "-d", "--daemon", "-b", "--bg", "--background", action="store_true", dest="daemon",
        help="Daemon mode: monitor airport continuously in background and send alerts to Telegram.",
    )
    parser.add_argument(
        "-f", "--foreground", "--fg", action="store_true", dest="foreground",
        help="Run daemon in foreground attached to current terminal session.",
    )
    parser.add_argument(
        "--stop", action="store_true", dest="stop",
        help="Stop running background daemon.",
    )
    parser.add_argument(
        "--status", action="store_true", dest="status",
        help="Show status of background daemon.",
    )
    parser.add_argument(
        "--logs", action="store_true", dest="logs",
        help="Show recent logs from background daemon.",
    )
    parser.add_argument(
        "-D", "--duration", "--runtime", metavar="DURATION", dest="duration", default=None,
        help=f"How long the daemon should run (e.g. '4', '4h', '30m', '1d', 'unlimited'). Default: {DEFAULT_DAEMON_DURATION_H}h.",
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
        "--history", action="store_true", dest="history",
        help="Show recent delay history from CSV log.",
    )
    parser.add_argument(
        "--no-history", action="store_true", dest="no_history",
        help="Disable CSV history logging for this run.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}",
    )
    return parser


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def print_header(airport: str, start: datetime, end: datetime,
                 min_delay: int, mode: str) -> None:
    label = airport_label(airport)
    print(f"\n{label}  –  {mode}")
    print(f"  Window:    {start.strftime('%Y-%m-%d %H:%M UTC')} → {end.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Min delay: {min_delay} min\n")


def main(argv: Optional[list[str]] = None) -> int:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=True)
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    load_dotenv()
    load_airport_cache()

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1

    # ── MANAGEMENT COMMANDS (--stop, --status, --logs, --history) ─────────
    if args.stop:
        return handle_stop_daemon()
    if args.status:
        return handle_status_daemon()
    if args.logs:
        return handle_logs_daemon()
    if args.history:
        return display_history(airport=args.airport)

    # Show help when no airport given (and not running --status/--logs/--stop/--history)
    if not args.airport:
        parser.print_help()
        return 0

    try:
        api_key   = _get_api_key()
        airport   = args.airport.strip().upper()
        min_delay = args.min_delay
        hours     = max(args.hours, 1)
        interval  = max(args.interval, 1)
        duration_sec = (
            parse_duration_to_seconds(args.duration)
            if args.duration is not None
            else (DEFAULT_DAEMON_DURATION_H * 3600)
        )

        bot_token, chat_id = get_telegram_config()

        # ── DAEMON MODE (Background by default) ───────────────────────────
        if args.daemon:
            if args.past:
                raise ValidationError("Cannot combine --daemon with --past.")
            if args.start or args.end:
                raise ValidationError("Cannot combine --daemon with --start/--end.")
            if args.telegram and not (bot_token and chat_id):
                raise ConfigError(
                    "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env to use --telegram / -t."
                )

            # Run in background by default unless --foreground/-f was specified
            if not args.foreground:
                raw_args = sys.argv[1:] if argv is None else argv
                return spawn_background_daemon(
                    raw_args,
                    airport=airport,
                    hours=hours,
                    interval_minutes=interval,
                    duration_seconds=duration_sec,
                    min_delay=min_delay,
                    telegram=args.telegram,
                    bot_token=bot_token,
                    chat_id=chat_id,
                )

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

                # Log to CSV history
                if not args.no_history:
                    append_to_history(flight, mode=mode_key, airport=airport)

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
