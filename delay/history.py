"""CSV history recording and terminal table display."""
from __future__ import annotations

import csv
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from delay.cache import airport_label
from delay.config import HISTORY_COLUMNS, HISTORY_FILE

if TYPE_CHECKING:
    from delay.models import Flight


def _ensure_history_header(filepath: Optional[str] = None) -> None:
    """Create CSV file with header row if it doesn't exist yet."""
    filepath = filepath or HISTORY_FILE
    if os.path.exists(filepath):
        return
    try:
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HISTORY_COLUMNS)
    except Exception:
        pass


def _fmt_utc(dt: Optional[datetime]) -> str:
    """Format datetime as compact UTC string for CSV, or empty string."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def append_to_history(
    flight: Flight,
    mode: str,
    airport: str,
    filepath: Optional[str] = None,
) -> None:
    """Append one delayed flight record to the CSV history file."""
    filepath = filepath or HISTORY_FILE
    _ensure_history_header(filepath)
    try:
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                mode,
                airport,
                flight.ident_iata or flight.ident,
                airport_label(flight.origin_code, flight.origin_name, flight.origin_city),
                airport_label(flight.destination_code, flight.destination_name, flight.destination_city),
                _fmt_utc(flight.scheduled_off),
                _fmt_utc(flight.estimated_off),
                _fmt_utc(flight.actual_off),
                flight.delay_minutes,
            ])
    except Exception as exc:
        print(f"  [history] Warning: could not write to {filepath}: {exc}", file=sys.stderr)


def _split_datetime_cell(val: str) -> tuple[str, str]:
    """Split 'YYYY-MM-DD HH:MM[:SS] UTC' into ('YYYY-MM-DD', 'HH:MM[:SS] UTC')."""
    val = (val or "").strip()
    if not val:
        return ("", "")
    parts = val.split(" ", 1)
    if len(parts) == 2 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0]):
        return (parts[0], parts[1])
    return (val, "")


def _split_airport_cell(val: str) -> tuple[str, str]:
    """Split 'CODE (Airport Name)' into ('CODE', 'Airport Name')."""
    val = (val or "").strip()
    if not val:
        return ("", "")
    m = re.match(r"^([A-Za-z0-9]+)\s*\((.+)\)$", val)
    if m:
        return (m.group(1), m.group(2))
    return (val, "")


def display_history(
    filepath: Optional[str] = None,
    tail: int = 30,
    airport: Optional[str] = None,
) -> int:
    """Print recent CSV history in a human-readable 2-line table format, optionally filtered by airport."""
    filepath = filepath or HISTORY_FILE
    if not os.path.exists(filepath):
        print(f"No history file found ({filepath}).")
        print("History is recorded automatically when delayed flights are found.")
        return 0

    try:
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            raw_rows = [row for row in reader if any(field.strip() for field in row)]
    except Exception as exc:
        print(f"Error reading history: {exc}", file=sys.stderr)
        return 1

    if len(raw_rows) <= 1:
        print("History file is empty (no delayed flights recorded yet).")
        return 0

    raw_header = [c.strip() for c in raw_rows[0]]
    raw_data   = raw_rows[1:]

    # Map column names to indices
    def get_col_idx(name: str, fallback: int) -> int:
        try:
            return raw_header.index(name)
        except ValueError:
            return fallback

    check_col = get_col_idx("check_time", 0)
    mode_col  = get_col_idx("mode", 1)
    airp_col  = get_col_idx("airport", 2)
    flt_col   = get_col_idx("flight", 3)
    orig_col  = get_col_idx("origin", 4 if len(raw_header) <= 10 else 5)
    dest_col  = get_col_idx("destination", 5 if len(raw_header) <= 10 else 6)
    sch_col   = get_col_idx("scheduled_off", 6 if len(raw_header) <= 10 else 7)
    est_col   = get_col_idx("estimated_off", 7 if len(raw_header) <= 10 else 8)
    act_col   = get_col_idx("actual_off", 8 if len(raw_header) <= 10 else 9)
    dly_col   = get_col_idx("delay_min", 9 if len(raw_header) <= 10 else 10)

    # Filter by airport if specified
    if airport:
        airport_upper = airport.strip().upper()
        raw_data = [row for row in raw_data if len(row) > airp_col and row[airp_col].strip().upper() == airport_upper]
        if not raw_data:
            print(f"No history entries for airport {airport_upper}.")
            return 0

    total = len(raw_data)

    # Show last N entries
    if len(raw_data) > tail:
        raw_data = raw_data[-tail:]
        print(f"(showing last {tail} of {total} entries)\n")

    # Format entries as 2 lines per column: (line1, line2)
    headers = [
        "check_time", "mode", "airport", "flight", "origin",
        "destination", "sched_off", "est_off", "act_off", "delay",
    ]

    entries: list[list[tuple[str, str]]] = []
    for row in raw_data:
        def get_val(idx: int) -> str:
            return row[idx].strip() if idx < len(row) else ""

        check_d, check_t = _split_datetime_cell(get_val(check_col))
        mode_val         = get_val(mode_col)
        airp_val         = get_val(airp_col)
        flt_val          = get_val(flt_col)
        orig_c, orig_n   = _split_airport_cell(get_val(orig_col))
        dest_c, dest_n   = _split_airport_cell(get_val(dest_col))
        sch_d, sch_t     = _split_datetime_cell(get_val(sch_col))
        est_d, est_t     = _split_datetime_cell(get_val(est_col))
        act_d, act_t     = _split_datetime_cell(get_val(act_col))
        dly_raw          = get_val(dly_col)
        dly_val          = f"+{dly_raw} min" if dly_raw.lstrip("-+").isdigit() and int(dly_raw) > 0 else (f"{dly_raw} min" if dly_raw else "")

        entry: list[tuple[str, str]] = [
            (check_d, check_t),
            (mode_val, ""),
            (airp_val, ""),
            (flt_val, ""),
            (orig_c, orig_n),
            (dest_c, dest_n),
            (sch_d, sch_t),
            (est_d, est_t),
            (act_d, act_t),
            (dly_val, ""),
        ]
        entries.append(entry)

    # Calculate column widths
    col_widths = []
    for i, h in enumerate(headers):
        max_w = len(h)
        for entry in entries:
            l1, l2 = entry[i]
            max_w = max(max_w, len(l1), len(l2))
        col_widths.append(max_w)

    # Print header
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(header_line)
    print("─" * len(header_line))

    # Print 2-line data rows
    for entry in entries:
        line1 = "  ".join(cell[0].ljust(col_widths[i]) for i, cell in enumerate(entry))
        line2 = "  ".join(cell[1].ljust(col_widths[i]) for i, cell in enumerate(entry))
        print(line1)
        if line2.strip():
            print(line2)

    filter_msg = f" for {airport.strip().upper()}" if airport else ""
    print(f"\n({total} entries{filter_msg} in {filepath})")
    return 0
