#!/usr/bin/env python3
"""
delayed_flights.py – FlightAware AeroAPI v4 CLI for finding delayed departures.

VERSION 0.0.1
"""
from __future__ import annotations

import os
import sys

# Ensure repository root is on sys.path when executed directly as a script
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from delay.cli import main

if __name__ == "__main__":
    sys.exit(main())
