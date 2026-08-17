"""Telegram Bot API integration and notification message formatting."""
from __future__ import annotations

import os
import sys
from typing import Optional

import requests

from delay.cache import airport_label
from delay.config import REQUEST_TIMEOUT
from delay.models import Flight


def get_telegram_config() -> tuple[Optional[str], Optional[str]]:
    """Retrieve Telegram bot token and chat ID from environment if configured."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
    return bot_token, chat_id


def format_telegram_flight(flight: Flight) -> str:
    """Format flight details into a rich HTML Telegram message."""
    origin_lbl = airport_label(flight.origin_code, flight.origin_name, flight.origin_city)
    dest_lbl   = airport_label(flight.destination_code, flight.destination_name, flight.destination_city)

    title = "⚠️ <b>DELAYED DEPARTURE</b>" if flight.is_past else "⏳ <b>PLANNED DEPARTURE DELAY</b>"
    display_ident = flight.ident_iata or flight.ident

    lines = [
        title,
        "",
        f"✈️ <b>Flight:</b> <code>{display_ident}</code>",
    ]
    if flight.ident_iata and flight.ident_iata != display_ident:
        lines.append(f"🏷️ <b>IATA:</b> {flight.ident_iata}")
    lines.append(f"🛫 <b>Origin:</b> {origin_lbl}")
    lines.append(f"🛬 <b>Destination:</b> {dest_lbl}")
    if flight.scheduled_off:
        lines.append(f"🕒 <b>Scheduled takeoff:</b> {flight._fmt(flight.scheduled_off)}")
    if flight.estimated_off:
        lines.append(f"⏱️ <b>Estimated takeoff:</b> {flight._fmt(flight.estimated_off)}")
    if flight.actual_off:
        lines.append(f"🚀 <b>Actual takeoff:</b> {flight._fmt(flight.actual_off)}")
    lines.append(f"🚨 <b>Delay:</b> <b>+{flight.delay_minutes} min</b>")
    return "\n".join(lines)


def format_telegram_no_delays(airport: str, duration_seconds: int, min_delay: int, hours: int) -> str:
    """Format Telegram message when monitoring finishes with no delays detected."""
    label = airport_label(airport)
    dur_h = duration_seconds / 3600
    if duration_seconds < 3600:
        mins = duration_seconds // 60
        s = duration_seconds % 60
        dur_str = f"{mins} min" if s == 0 else f"{mins}m {s}s"
    elif dur_h.is_integer():
        dur_str = f"{int(dur_h)} h"
    else:
        dur_str = f"{dur_h:.2f} h"

    lines = [
        "✅ <b>DELAY MONITORING FINISHED</b>",
        "",
        f"📍 <b>Airport:</b> {label}",
        f"⏱️ <b>Monitored duration:</b> {dur_str}",
        f"🔎 <b>Future window:</b> +{hours} h",
        f"⚠️ <b>Min delay threshold:</b> ≥ {min_delay} min",
        "",
        "ℹ️ <b>No delayed flights were found during this period.</b>",
    ]
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
