"""Data models for flights and query statistics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from delay.cache import airport_label, fmt_local


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
            "ident":                self.ident_iata or self.ident,
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
        display_ident = self.ident_iata or self.ident
        lines.append(f"  Flight:            {display_ident}")
        if self.ident_iata and self.ident_iata != display_ident:
            lines.append(f"  Flight (IATA):     {self.ident_iata}")
        if self.ident_icao and self.ident_icao != display_ident:
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
