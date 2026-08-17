# Delay

CLI & Background Daemon tool in Python 3 querying **FlightAware AeroAPI v4** to track delayed flight departures with instant **Telegram Bot** alerts.

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
   - Runs continuously in the **background** by default (safe to close terminal)
   - Periodically checks the airport every **N minutes** (`-i` / `--interval`, default: 30m)
   - Bounded runtime duration support (`-D` / `--duration`, default: `4h`, e.g. `6h`, `30m`, `1d`, `unlimited`)
   - Sends Telegram notifications immediately when new delayed flights appear (with deduplication)
   - Sends a summary notification on completion if no delayed flights were found during the monitoring window
   - Built-in management commands: `delay --status`, `delay --logs`, `delay --stop`

4. **Dynamic Airport Resolution & Persistent Cache**:
   - Fetches official airport names, cities, and timezones directly from AeroAPI
   - Preferentially resolves clean IATA codes (e.g. `TFS`, `WAW`, `LPA`)
   - Caches airport data locally on disk (`.airports_cache.json`) to conserve API credits

5. **CSV Delay History Logging**:
   - Automatically logs all detected delayed flights to `delay_history.csv`
   - Formatted 2-line terminal table viewer (`delay --history`, optionally filtered by airport `delay -a TFS --history`)
   - Can be disabled for individual runs via `--no-history`

6. **Telegram Bot Integration**:
   - Formatted HTML alerts sent directly to your Telegram chat/channel
   - Shows flight identifier, route, origin/destination, scheduled/estimated takeoff times, local times, and delay

---

## Installation & Setup

```bash
# 1. Clone repository
git clone https://github.com/ferges123/delay.git /opt/delay
cd /opt/delay

# 2. Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Add CLI alias to your ~/.bashrc (optional, for direct 'delay' command)
echo "alias delay='/opt/delay/.venv/bin/python3 /opt/delay/delayed_flights.py'" >> ~/.bashrc
source ~/.bashrc
```

---

## Configuration (`.env`)

Create or edit your `.env` file in the project folder:

```bash
# FlightAware AeroAPI Key (Required)
FLIGHTAWARE_API_KEY=your_aeroapi_key_here

# Telegram Bot (Optional, for -d daemon alerts and -t)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=123456789
```

> **How to get Telegram Bot credentials:**
> 1. Message **@BotFather** on Telegram -> `/newbot` -> get `TELEGRAM_BOT_TOKEN`.
> 2. Start a chat with your bot, then get your chat ID from **@userinfobot** -> `TELEGRAM_CHAT_ID`.

---

## Usage Examples

### 1. Monitor / Daemon mode with Telegram alerts (`-d`)

> **Note:** Daemon mode (`-d`) automatically runs in the background as a detached process, so you can safely close your terminal.

```bash
# Monitor TFS in background for 6 hours with Telegram notifications (-t)
delay -a TFS -d -D 6 -t

# Monitor TFS in background with default 4h duration
delay -a TFS -d -t

# Check if background daemon is running + see recent logs
delay --status

# View live daemon logs
delay --logs

# Stop the background daemon
delay --stop

# Monitor Warsaw Chopin in background for 8 hours, checking every 15 min
delay -a WAW -d -D 8h -i 15 -w 6 -t

# Run daemon continuously without time limit
delay -a WAW -d -D unlimited -t

# Run daemon in foreground (attached to terminal)
delay -a WAW -d -f
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

### 4. Delay History Log (`--history`)

```bash
# View recent recorded delayed flights history
delay --history

# View history filtered for a specific airport
delay -a TFS --history
```

---

## CLI Options

| Option | Shorthand | Default | Description |
|---|---|---|---|
| `--airport` | `-a` | *None* | Airport IATA or ICAO code (required, e.g. `WAW`, `LPA`, `TFS`) |
| `--daemon` | `-d` | *off* | Run continuous monitoring daemon in background |
| `--foreground` | `-f`, `--fg` | *off* | Run daemon in foreground attached to current terminal |
| `--status` | | | Show status of running background daemon |
| `--logs` | | | View recent logs from background daemon |
| `--stop` | | | Stop running background daemon |
| `--duration` | `-D` | `4h` | Total runtime duration for daemon mode (e.g. `4`, `4h`, `30m`, `1d`, `unlimited`) |
| `--hours` / `--window` | `-w` | `6` | Future window in hours for upcoming flights |
| `--interval` | `-i` | `30` | Interval in minutes between checks in daemon mode |
| `--past` | `-p` | *off* | Search past 24h for actual delayed departures |
| `--telegram` | `-t` | *off* | Send alert to Telegram for delayed flights |
| `--min-delay` | | `60` | Minimum delay threshold in minutes |
| `--all` | | *off* | Return all delayed flights instead of stopping after first |
| `--json` | | *off* | Format output as JSON |
| `--history` | | | Show recent delay history from CSV log |
| `--no-history` | | *off* | Disable CSV history logging for this run |
| `--version` | | | Show version number |
| `--help` | `-h` | | Show help message |
