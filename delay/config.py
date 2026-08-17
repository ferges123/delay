"""Configuration constants and paths."""
from __future__ import annotations

import os

VERSION = "0.0.1"

BASE_URL          = "https://aeroapi.flightaware.com/aeroapi"
DEFAULT_MIN_DELAY = 60      # minutes
REQUEST_TIMEOUT   = 30      # seconds
MAX_HISTORY_DAYS  = 10      # AeroAPI personal plan limit
DEFAULT_FUTURE_H  = 6       # hours ahead for upcoming / daemon mode
DEFAULT_INTERVAL  = 30      # minutes between checks in daemon mode
DEFAULT_DAEMON_DURATION_H = 4  # hours daemon runs by default if -D is not specified

# Project directory
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Airport cache file
DEFAULT_CACHE_FILE = os.path.join(PROJECT_DIR, ".airports_cache.json")
AIRPORT_CACHE_FILE = os.environ.get("DELAY_AIRPORT_CACHE_FILE", DEFAULT_CACHE_FILE)

# History CSV file
DEFAULT_HISTORY_FILE = os.path.join(PROJECT_DIR, "delay_history.csv")
HISTORY_FILE = os.environ.get("DELAY_HISTORY_FILE", DEFAULT_HISTORY_FILE)

HISTORY_COLUMNS = [
    "check_time",
    "mode",
    "airport",
    "flight",
    "origin",
    "destination",
    "scheduled_off",
    "estimated_off",
    "actual_off",
    "delay_min",
]

# Daemon runtime files
DAEMON_PID_FILE = os.path.join(PROJECT_DIR, ".daemon.pid")
DAEMON_LOG_FILE = os.path.join(PROJECT_DIR, "daemon.log")
