"""Background daemon lifecycle management and continuous monitoring loop."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from delay.api import fetch_airport_info, iter_upcoming_delayed
from delay.cache import airport_label
from delay.config import (
    DAEMON_LOG_FILE,
    DAEMON_PID_FILE,
    DEFAULT_DAEMON_DURATION_H,
    DEFAULT_FUTURE_H,
    DEFAULT_INTERVAL,
    DEFAULT_MIN_DELAY,
    PROJECT_DIR,
)
from delay.exceptions import AppError
from delay.history import append_to_history
from delay.models import Stats
from delay.telegram import (
    format_telegram_flight,
    format_telegram_no_delays,
    get_telegram_config,
    send_telegram_message,
)


def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def get_running_daemon_pid() -> Optional[int]:
    """Return PID of active background daemon if running."""
    if os.path.exists(DAEMON_PID_FILE):
        try:
            with open(DAEMON_PID_FILE, "r") as f:
                pid = int(f.read().strip())
            if is_pid_alive(pid):
                return pid
        except Exception:
            pass
    return None


def handle_stop_daemon() -> int:
    """Stop currently running background daemon."""
    pid = get_running_daemon_pid()
    if pid is None:
        print("No active background daemon process found.")
        if os.path.exists(DAEMON_PID_FILE):
            try:
                os.remove(DAEMON_PID_FILE)
            except Exception:
                pass
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
        if os.path.exists(DAEMON_PID_FILE):
            try:
                os.remove(DAEMON_PID_FILE)
            except Exception:
                pass
        print(f"✓ Stopped background daemon process (PID: {pid}).")
        return 0
    except Exception as exc:
        print(f"Error stopping process {pid}: {exc}", file=sys.stderr)
        return 1


def handle_status_daemon() -> int:
    """Show live status of background daemon."""
    pid = get_running_daemon_pid()
    if pid is None:
        print("Status: Daemon is NOT running.")
        return 0
    print(f"Status: Daemon is RUNNING in background (PID: {pid}).")
    print(f"Log file: {DAEMON_LOG_FILE}")
    if os.path.exists(DAEMON_LOG_FILE):
        print("\n--- Recent logs (last 10 lines) ---")
        try:
            with open(DAEMON_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                for line in lines[-10:]:
                    print(line.rstrip())
        except Exception:
            pass
    return 0


def handle_logs_daemon(tail: int = 30) -> int:
    """Print recent logs from background daemon."""
    if not os.path.exists(DAEMON_LOG_FILE):
        print(f"Log file not found ({DAEMON_LOG_FILE}).")
        return 0
    try:
        with open(DAEMON_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            for line in lines[-tail:]:
                print(line.rstrip())
    except Exception as exc:
        print(f"Error reading log file: {exc}", file=sys.stderr)
    return 0


def spawn_background_daemon(
    argv: list[str],
    airport: Optional[str] = None,
    hours: int = DEFAULT_FUTURE_H,
    interval_minutes: int = DEFAULT_INTERVAL,
    duration_seconds: Optional[int] = DEFAULT_DAEMON_DURATION_H * 3600,
    min_delay: int = DEFAULT_MIN_DELAY,
    telegram: bool = False,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> int:
    """Fork/spawn child process detached from terminal session."""
    from delay.cli import build_parser, parse_duration_to_seconds

    pid = get_running_daemon_pid()
    if pid is not None:
        print(f"⚠️ Daemon is already running in background (PID: {pid}).")
        print("  Check status: delay --status")
        print("  Stop daemon:  delay --stop")
        return 1

    if airport is None or not telegram:
        try:
            parsed = build_parser().parse_args(argv)
            if airport is None and parsed.airport:
                airport = parsed.airport.strip().upper()
            if not telegram and parsed.telegram:
                telegram = True
            hours = max(parsed.hours, 1)
            interval_minutes = max(parsed.interval, 1)
            if parsed.duration is not None:
                duration_seconds = parse_duration_to_seconds(parsed.duration)
            else:
                duration_seconds = DEFAULT_DAEMON_DURATION_H * 3600
            min_delay = parsed.min_delay
        except Exception:
            pass

    if bot_token is None or chat_id is None:
        b_tok, c_id = get_telegram_config()
        bot_token = bot_token or b_tok
        chat_id = chat_id or c_id

    clean_args = [a for a in argv if a not in ("-f", "--foreground", "--fg")]
    if "-d" not in clean_args and "--daemon" not in clean_args:
        clean_args.append("-d")
    clean_args.append("-f")  # Child runs loop in foreground of its detached session

    # Entry target: delayed_flights.py in PROJECT_DIR or module invocation
    entry_script = os.path.join(PROJECT_DIR, "delayed_flights.py")
    if os.path.exists(entry_script):
        cmd = [sys.executable, "-u", entry_script] + clean_args
    else:
        cmd = [sys.executable, "-u", "-m", "delay"] + clean_args

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{PROJECT_DIR}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = PROJECT_DIR

    try:
        out_f = open(DAEMON_LOG_FILE, "a", encoding="utf-8")
        out_f.write(f"\n=== Daemon start: {datetime.now(tz=timezone.utc).isoformat()} ===\n")
        out_f.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=out_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,  # setsid: fully detached from terminal session
        )
        out_f.close()

        with open(DAEMON_PID_FILE, "w") as pf:
            pf.write(str(proc.pid))

        if duration_seconds:
            dur_h = duration_seconds / 3600
            deadline = datetime.now(tz=timezone.utc) + timedelta(seconds=duration_seconds)
            deadline_str = deadline.strftime("%Y-%m-%d %H:%M:%S UTC")
            if duration_seconds < 3600:
                dur_str = f"{duration_seconds // 60} min"
            elif dur_h.is_integer():
                dur_str = f"{int(dur_h)} h"
            else:
                dur_str = f"{dur_h:.2f} h"
            dur_display = f"{dur_str} (until {deadline_str})"
        else:
            dur_display = "Unlimited (until stopped with delay --stop)"

        print(f"✓ Daemon started in background (PID: {proc.pid})")
        if airport:
            print(f"  Airport:        {airport_label(airport)}")
        print(f"  Check interval: every {interval_minutes} min")
        print(f"  Duration:       {dur_display}")
        print(f"  Future window:  +{hours} h (min delay: >= {min_delay} min)")
        if telegram:
            if bot_token and chat_id:
                print(f"  Notifications:  Telegram (chat_id: {chat_id})")
            else:
                print("  Notifications:  Telegram (⚠️ token/chat_id missing in .env)")
        else:
            print("  Notifications:  Console/logs only (use -t for Telegram)")
        print(f"  Live logs:      delay --logs  (or: tail -f {DAEMON_LOG_FILE})")
        print(f"  Status:         delay --status")
        print(f"  Stop:           delay --stop")
        print("  You can now safely close the terminal! 🚀")
        return 0
    except Exception as exc:
        print(f"Failed to spawn background process: {exc}", file=sys.stderr)
        return 1


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

    my_pid = os.getpid()
    try:
        with open(DAEMON_PID_FILE, "w") as pf:
            pf.write(str(my_pid))
    except Exception:
        pass

    daemon_start = datetime.now(tz=timezone.utc)
    deadline     = daemon_start + timedelta(seconds=duration_seconds) if duration_seconds else None

    print(f"\n🚀 [DAEMON] Started delay monitoring for {label}")
    print(f"  Future window:        +{hours} h")
    print(f"  Check interval:       every {interval_minutes} min")
    print(f"  Minimum delay:        >= {min_delay} min")
    if duration_seconds:
        dur_h = duration_seconds / 3600
        print(f"  Runtime duration:     {dur_h:.2f} h (until {deadline.strftime('%Y-%m-%d %H:%M:%S UTC') if deadline else ''})")
    else:
        print("  Runtime duration:     Unlimited (until stopped with Ctrl+C or delay --stop)")

    if bot_token and chat_id:
        print(f"  Notifications:        Telegram (chat_id: {chat_id})")
    else:
        print("  Notifications:        Console only (no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    print("  Press Ctrl+C or run delay --stop to stop monitoring.\n")

    notified_flights: set[str] = set()

    try:
        while True:
            cycle_start = datetime.now(tz=timezone.utc)
            if deadline and cycle_start >= deadline:
                print(f"\n⏰ [DAEMON] Runtime duration limit reached ({duration_seconds/3600:.2f} h). Stopped monitoring.")
                break

            start = cycle_start
            end   = cycle_start + timedelta(hours=hours)
            ts_str = cycle_start.strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"[{ts_str}] Checking departures for {airport} in window {start.strftime('%H:%M')} – {end.strftime('%H:%M UTC')}…")

            stats = Stats()
            try:
                for flight in iter_upcoming_delayed(
                    session, api_key, airport, start, end, min_delay, stop_at_first=False, stats=stats
                ):
                    flight_key = f"{flight.ident}_{flight.scheduled_off.isoformat() if flight.scheduled_off else ''}_{flight.delay_minutes}"
                    if flight_key not in notified_flights:
                        notified_flights.add(flight_key)
                        print(f"\n🚨 NEW DELAY DETECTED: {flight.ident} (+{flight.delay_minutes} min)")
                        print(flight.display("UPCOMING DELAYED FLIGHT"))
                        print()

                        # Log to CSV history
                        append_to_history(flight, mode="daemon", airport=airport)

                        if bot_token and chat_id:
                            msg = format_telegram_flight(flight)
                            success = send_telegram_message(bot_token, chat_id, msg, session=session)
                            if success:
                                print(f"  ✓ Sent Telegram notification for {flight.ident}")
                            else:
                                print(f"  ✗ Error sending Telegram notification for {flight.ident}", file=sys.stderr)

                print(f"  Analyzed {stats.flights_analyzed} flights, delayed found: {stats.flights_found}.")

            except AppError as err:
                print(f"  [API ERROR in cycle]: {err}", file=sys.stderr)
            except Exception as exc:
                print(f"  [ERROR in cycle]: {exc}", file=sys.stderr)

            now_after = datetime.now(tz=timezone.utc)
            if deadline and now_after >= deadline:
                print(f"\n⏰ [DAEMON] Runtime duration limit reached ({duration_seconds/3600:.2f} h). Stopped monitoring.")
                break

            # Calculate sleep seconds (don't sleep past deadline)
            sleep_seconds = interval_minutes * 60
            if deadline:
                sec_left = int((deadline - now_after).total_seconds())
                if sec_left <= 0:
                    print(f"\n⏰ [DAEMON] Runtime duration limit reached ({duration_seconds/3600:.2f} h). Stopped monitoring.")
                    break
                sleep_seconds = min(sleep_seconds, sec_left)

            mins = sleep_seconds // 60
            secs = sleep_seconds % 60
            time_msg = f"{mins} min" if secs == 0 else f"{mins} min {secs} s"
            print(f"  Next check in {time_msg}…\n")

            for _ in range(sleep_seconds):
                time.sleep(1)

        # Monitoring completed due to runtime duration limit
        if duration_seconds and len(notified_flights) == 0:
            dur_h = duration_seconds / 3600
            print(f"\nℹ️ [DAEMON] Monitoring finished ({dur_h:.2f} h). No delayed flights (>= {min_delay} min) were detected.")
            if bot_token and chat_id:
                msg = format_telegram_no_delays(airport, duration_seconds, min_delay, hours)
                success = send_telegram_message(bot_token, chat_id, msg, session=session)
                if success:
                    print("  ✓ Sent Telegram summary notification: No delayed flights found.")
                else:
                    print("  ✗ Error sending Telegram summary notification.", file=sys.stderr)
    finally:
        if os.path.exists(DAEMON_PID_FILE):
            try:
                with open(DAEMON_PID_FILE, "r") as pf:
                    if pf.read().strip() == str(my_pid):
                        os.remove(DAEMON_PID_FILE)
            except Exception:
                pass
