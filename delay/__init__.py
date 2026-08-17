"""
delay – FlightAware AeroAPI v4 departure delay finder & Telegram notifier.
"""
from __future__ import annotations

from delay.cli import main
from delay.config import VERSION
from delay.models import Flight, Stats

__all__ = ["main", "VERSION", "Flight", "Stats"]
