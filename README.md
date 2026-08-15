# Delayed Flights Finder

A Python 3 CLI tool that queries **FlightAware AeroAPI v4** to find departures
delayed by at least N minutes at a given airport.

---

## How it works

The tool calls the AeroAPI endpoint:

```
GET /airports/{id}/flights/departures
```

with `start` / `end` parameters (ISO 8601) and iterates over the returned
`departures` array. For each flight it computes:

```
delay = actual_off − scheduled_off
```

A flight is reported if `delay >= --min-delay` (default 60 minutes).
Pagination is handled automatically via the `links.next` cursor.

### Why `actual_off` and not `actual_out`?

| Field | Meaning |
|---|---|
| `scheduled_out` / `actual_out` | Gate departure (pushback) |
| `scheduled_off` / `actual_off` | **Runway takeoff** ← used here |
| `scheduled_on`  / `actual_on`  | Runway landing at destination |
| `scheduled_in`  / `actual_in`  | Gate arrival at destination |

Airlines report departure delays based on the **runway** takeoff time, not the
gate time. Using `actual_off − scheduled_off` matches the industry standard
and avoids inflating delays due to long taxi times.

---

## Requirements

- Python 3.8+
- FlightAware AeroAPI key (Personal plan or higher)

---

## Installation

```bash
# Clone / download the project
cd /opt/delay

# Create virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## API key setup

1. Register at <https://www.flightaware.com/commercial/aeroapi/>
2. Create an API key in the AeroAPI portal
3. Export it in your shell **or** create a `.env` file:

```bash
# Option A – shell environment variable
export FLIGHTAWARE_API_KEY=your_key_here

# Option B – .env file (copied from .env.example)
cp .env.example .env
# then edit .env and replace "your_api_key_here" with the real key
```

The `.env` file is loaded automatically by `python-dotenv`.
It is listed in `.gitignore` – **never commit it**.

---

## Usage

### Defaults (Tenerife South, last 24-hour window, 60-minute threshold)

```bash
python delayed_flights.py
```

### Custom airport and time window

```bash
python delayed_flights.py \
  --airport EPWA \
  --start 2026-08-10T00:00:00Z \
  --end   2026-08-11T00:00:00Z \
  --min-delay 60
```

### All matching flights instead of just the first

```bash
python delayed_flights.py --airport GCTS --all
```

### JSON output

```bash
python delayed_flights.py --airport GCTS --json
```

### Custom delay threshold (30 minutes)

```bash
python delayed_flights.py --airport LEMD --min-delay 30
```

### Combine options

```bash
python delayed_flights.py \
  --airport EGLL \
  --start 2026-08-10T00:00:00Z \
  --end   2026-08-11T00:00:00Z \
  --min-delay 45 \
  --all \
  --json
```

---

## All CLI arguments

| Argument | Default | Description |
|---|---|---|
| `--airport ICAO` | `GCTS` | Airport ICAO code |
| `--start ISO8601` | 48 h ago | Start of window (inclusive, must include TZ) |
| `--end ISO8601` | 24 h ago | End of window (exclusive, must include TZ) |
| `--min-delay N` | `60` | Minimum delay in minutes |
| `--all` | off | Show all matching flights |
| `--json` | off | Output as JSON |

---

## Example output (text)

```
Searching departures from GCTS (Tenerife South)
  Window: 2026-08-13 00:00 UTC → 2026-08-14 00:00 UTC
  Min delay: 60 minutes

FOUND DELAYED FLIGHT

  Flight:            VY1234
  Origin:            GCTS (Tenerife South)
  Destination:       EGSS (London Stansted)
  Scheduled takeoff: 2026-08-13 14:35 UTC
  Actual takeoff:    2026-08-13 16:02 UTC
  Delay:             87 minutes

────────────────────────────────────────────
STATISTICS
  HTTP requests made:    2
  Flights analyzed:      45
  Skipped (missing ts):  3
  Delayed flights found: 1
```

## Example output (JSON)

```json
{
  "airport": "GCTS",
  "window_start": "2026-08-13T00:00:00Z",
  "window_end": "2026-08-14T00:00:00Z",
  "min_delay_minutes": 60,
  "flights_found": 1,
  "delayed_flights": [
    {
      "ident": "VY1234",
      "ident_iata": "VY1234",
      "ident_icao": "VLG1234",
      "origin": "GCTS (Tenerife South)",
      "destination": "EGSS (London Stansted)",
      "scheduled_off": "2026-08-13 14:35 UTC",
      "estimated_off": null,
      "actual_off": "2026-08-13 16:02 UTC",
      "delay_minutes": 87
    }
  ],
  "stats": {
    "requests_made": 2,
    "flights_analyzed": 45,
    "flights_skipped": 3
  }
}
```

---

## AeroAPI limitations and costs

| Topic | Detail |
|---|---|
| **History window** | `start` / `end` must be within **10 days past** and 2 days future (Personal plan). Older data requires the `GET /history/airports/{id}/flights/departures` endpoint (higher cost). |
| **Pagination** | Each page returns up to ~15 flights. The tool follows `links.next` automatically. Each page = 1 API credit. |
| **Rate limits** | Personal plan: limited credits/month. The tool performs one gentle retry on HTTP 429, respecting `Retry-After`. |
| **No live/future** | This endpoint returns **already-departed** flights (`actual_off` is populated). For scheduled departures use `/airports/{id}/flights/scheduled_departures`. |
| **Cost minimization** | Without `--all` the tool stops after the first delayed flight found, minimising API calls. |

---

## Running tests

```bash
pytest test_delayed_flights.py -v
```

No network requests are made during tests – all AeroAPI calls are mocked.

---

## Project structure

```
/opt/delay/
├── delayed_flights.py       # Main CLI application
├── test_delayed_flights.py  # Automated tests (pytest)
├── requirements.txt         # Python dependencies
├── .env.example             # Template for API key
├── .gitignore               # Excludes .env and build artifacts
└── README.md                # This file
```
