# Delay (v0.0.1)

A Python 3 CLI tool that queries **FlightAware AeroAPI v4** to find upcoming or past delayed departures at a given airport.

---

## How it works

The tool operates in two main modes:

1. **Upcoming mode (default)**:
   - Endpoint: `GET /airports/{id}/flights/scheduled_departures`
   - Window: next **9 hours** from current time
   - Condition: flights that have not yet reached their scheduled takeoff time (`scheduled_off >= now`) and have a planned departure delay (`departure_delay >= --min-delay`).

2. **Past mode (`-p` / `--past`)**:
   - Endpoint: `GET /airports/{id}/flights/departures`
   - Window: last **24 hours** (or custom `--start`/`--end` 24h window)
   - Condition: departed flights where `actual_off - scheduled_off >= --min-delay`.

Times are displayed in **UTC** alongside the airport's **local time** (e.g. `14:35 UTC (16:35 CEST)`).

---

## Requirements

- Python 3.8+
- FlightAware AeroAPI key (Personal plan or higher)

---

## Installation & Setup

```bash
# Clone / navigate to directory
cd /opt/delay

# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure API Key
cp .env.example .env
# Edit .env and set your key: FLIGHTAWARE_API_KEY=your_key_here
```

### Alias configuration

Add an alias to `~/.bashrc`:
```bash
alias delay='/opt/delay/.venv/bin/python3 /opt/delay/delayed_flights.py'
```

---

## Usage

If invoked without `-a`, the application displays the help message:
```bash
delay
```

### 1. Upcoming delayed flights (Next 9 hours)

```bash
# Check planned delays for Warsaw Chopin
delay -a WAW

# Lower threshold (e.g. >= 30 min) and show all matching flights
delay -a WAW --min-delay 30 --all

# Output as JSON
delay -a WAW --json
```

### 2. Past delayed flights (Last 24 hours)

```bash
# Search actual delays in the last 24h
delay -a WAW -p

# All delayed flights in the last 24h
delay -a LPA -p --all

# Custom 24-hour window
delay -a TFS -p --start 2026-08-10T00:00:00Z --end 2026-08-11T00:00:00Z
```

---

## CLI Options

| Option | Shorthand | Default | Description |
|---|---|---|---|
| `--airport` | `-a` | *None* | Airport IATA or ICAO code (required, e.g. `WAW`, `LPA`, `TFS`) |
| `--past` | `-p` | *off* | Search past 24h for actual delayed departures |
| `--min-delay` | | `60` | Minimum delay threshold in minutes |
| `--all` | | *off* | Return all delayed flights instead of stopping after first |
| `--json` | | *off* | Format output as JSON |
| `--start` | | *None* | Start ISO8601 datetime (past mode only) |
| `--end` | | *None* | End ISO8601 datetime (past mode only) |
| `--version` | | | Show version number (`0.0.1`) |
| `--help` | `-h` | | Show help message |

---

## Running Tests

```bash
pytest test_delayed_flights.py -v
```

All API requests are mocked — no real API credits are consumed during tests.
