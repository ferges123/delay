# Delay (v0.0.1)

CLI & Daemon tool in Python 3 querying **FlightAware AeroAPI v4** to track delayed flight departures with instant **Telegram Bot** alerts.

---

## Features

1. **Upcoming mode (default)**:
   - Scans departures for the next **N hours** (`-w` / `--hours`, default: 6h)
   - Filters only flights that haven't departed yet according to schedule (`scheduled_off >= now`)
   - Identifies planned delays (`departure_delay >= --min-delay`)

2. **Past mode (`-p` / `--past`)**:
   - Scans actual departures in the last **24 hours** (or custom `--start`/`--end`)
   - Verifies actual delays (`actual_off - scheduled_off >= --min-delay`)

3. **Daemon / Monitor mode (`-d` / `--daemon`)**:
   - Runs continuously in the background
   - Periodically checks the airport every **N minutes** (`-i` / `--interval`, default: 30m)
   - Checks the next **W hours** (`-w` / `--hours`, default: 6h)
   - Sends Telegram notifications immediately when new delayed flights appear (with deduplication)

4. **Telegram Bot Integration**:
   - Formatted HTML alerts sent directly to your Telegram chat/channel
   - Shows flight identifier, route, origin/destination, scheduled/estimated takeoff times, local times, and delay

---

## Configuration (`.env`)

Create or edit your `.env` file in the project folder:

```bash
# FlightAware AeroAPI Key
FLIGHTAWARE_API_KEY=your_aeroapi_key_here

# Telegram Bot (Optional, for -d daemon alerts and --telegram)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=123456789
```

> **How to get Telegram Bot credentials:**
> 1. Message **@BotFather** on Telegram -> `/newbot` -> get `TELEGRAM_BOT_TOKEN`.
> 2. Start a chat with your bot, then get your chat ID from **@userinfobot** -> `TELEGRAM_CHAT_ID`.

---

## Usage Examples

### 1. Monitor / Daemon mode with Telegram alerts (`-d`)

```bash
# Run daemon in the background (-b) for 4 hours (-D 4h) — safe to close terminal!
delay -a TFS -d -D 4h -b

# Check if background daemon is running + see recent logs
delay --status

# View live daemon logs
delay --logs

# Stop the background daemon
delay --stop

# Monitor Warsaw Chopin for 8 hours total, checking every 15 min in next 6h window
delay -a WAW -d -D 8h -i 15 -w 6 -b
```

### 2. Single-run (Upcoming departures)

```bash
# Check planned delays for Warsaw in the next 6 hours
delay -a WAW

# Check next 9 hours and show all matching flights
delay -a WAW -w 9 --all

# Single run with Telegram alert
delay -a WAW -t

# JSON format
delay -a WAW --json
```

### 3. Past departures (`-p`)

```bash
# Search actual delays in the last 24h
delay -a WAW -p

# Custom 24-hour historical window
delay -a TFS -p --start 2026-08-10T00:00:00Z --end 2026-08-11T00:00:00Z
```

---

## CLI Options

| Option | Shorthand | Default | Description |
|---|---|---|---|
| `--airport` | `-a` | *None* | Airport IATA or ICAO code (required, e.g. `WAW`, `LPA`, `TFS`) |
| `--daemon` | `-d` | *off* | Run continuous monitoring daemon |
| `--background` | `-b`, `--bg` | *off* | Run daemon in background (detached process). Safe to close terminal |
| `--status` | | | Show status of running background daemon |
| `--logs` | | | View recent logs from background daemon |
| `--stop` | | | Stop running background daemon |
| `--duration` | `-D` | *None* | Total runtime duration for daemon mode (e.g. `4`, `4h`, `30m`, `1d`) |
| `--hours` / `--window` | `-w` | `6` | Future window in hours for upcoming flights |
| `--interval` | `-i` | `30` | Interval in minutes between checks in daemon mode |
| `--past` | `-p` | *off* | Search past 24h for actual delayed departures |
| `--telegram` | `-t` | *off* | Send alert to Telegram for delayed flights |
| `--min-delay` | | `60` | Minimum delay threshold in minutes |
| `--all` | | *off* | Return all delayed flights instead of stopping after first |
| `--json` | | *off* | Format output as JSON |
| `--version` | | | Show version number (`0.0.1`) |
| `--help` | `-h` | | Show help message |

---

## Running Tests

```bash
pytest test_delayed_flights.py -v
```
