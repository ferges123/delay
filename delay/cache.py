"""Dynamic & persistent airport cache and timezone formatting helpers."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    ZoneInfo = None                      # type: ignore[assignment,misc]
    ZoneInfoNotFoundError = Exception    # type: ignore[assignment,misc]

from delay.config import AIRPORT_CACHE_FILE

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


def cache_airport(
    code: Optional[str],
    name: Optional[str] = None,
    city: Optional[str] = None,
    persist: bool = True,
) -> None:
    """Cache airport display label dynamically from API data and save to disk."""
    if not code:
        return
    code_upper = code.strip().upper()
    display = name or city
    if display and AIRPORT_CACHE.get(code_upper) != display:
        AIRPORT_CACHE[code_upper] = display
        if persist:
            save_airport_cache()


def airport_label(
    code: Optional[str],
    name: Optional[str] = None,
    city: Optional[str] = None,
) -> str:
    """Return 'CODE (Airport Name / City)' or just 'CODE' using dynamic & cached AeroAPI data."""
    if not code:
        return "Unknown"
    code_upper = code.strip().upper()
    display_code = AIRPORT_CACHE.get(f"IATA_FOR_{code_upper}") or code_upper
    display = name or city or AIRPORT_CACHE.get(code_upper)
    if display:
        cache_airport(code_upper, display)
        return f"{display_code} ({display})"
    return display_code


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
