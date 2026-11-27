# Nazbeen MT5 Market Data API

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009485?logo=fastapi&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green.svg)

A **local, read-only** market data API for MetaTrader 5. It exposes REST and WebSocket endpoints backed by a logged-in MT5 terminal, with a shared quote collector, UTC-correct history, Windows autostart, local diagnostics, and an optional Caddy reverse proxy.

> **Private by default.** The service binds to loopback, requires an API key for all market endpoints, and never places, modifies, or closes trades. If MT5 is unavailable, the API reports the problem — it never invents prices.

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Broker time and UTC](#broker-time-and-utc)
- [Local domain with Caddy](#local-domain-with-caddy)
- [Authentication](#authentication)
- [API reference](#api-reference)
- [Client examples](#client-examples)
- [Windows autostart](#windows-autostart)
- [Diagnostics](#diagnostics)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Security](#security)
- [Uninstall](#uninstall)
- [License](#license)

---

## Features

- **REST + WebSocket** market data over FastAPI.
- **Broker-native symbols only** — no invented tickers or synthetic prices.
- **UTC-correct timestamps** for quotes, candles, ticks, and history ranges.
- **Completed vs forming candles** reported explicitly.
- **Serialized MT5 access** through one re-entrant connection manager.
- **Shared quote collector** for REST and WebSocket clients.
- **Private API key** for REST headers and WebSocket query strings.
- **User-level Windows autostart** with battery-safe Task Scheduler settings.
- **Optional local domain** via Caddy at `http://mt5.nazbeen.test`.
- **SQLite-backed diagnostics** and local PowerShell status tools.

---

## Architecture

```text
Browser / curl / Python / Next.js
               |
               v
  http://mt5.nazbeen.test       (optional Caddy, loopback only)
               |
               v
      FastAPI on 127.0.0.1:8080
       |          |          |
       |          |          +--> WebSocket subscriptions
       |          +-------------> Shared quote collector
       +-----------------------> Serialized MT5 bridge --> logged-in terminal
                                      |
                                      +--> SQLite diagnostics
```

| Component | Responsibility |
|---|---|
| FastAPI app | HTTP API, WebSocket stream, validation, error envelopes |
| MT5 bridge | One serialized connection manager for all terminal calls |
| BrokerClock | Converts broker-server time to real UTC |
| MarketCollector | Polls the active symbol set once for all clients |
| SQLite | Local operational/diagnostic records |
| Caddy | Friendly local hostname; no public TLS or exposure |

---

## Requirements

- **Windows** — required by the MetaTrader 5 Python bridge.
- **Python 3.12+**.
- **MetaTrader 5 terminal**, installed and logged in to the intended broker account.
- **Caddy** — optional, for `http://mt5.nazbeen.test`.
- **Administrator rights** — only for the hosts-file entry. Scheduled tasks are user-level.

The API is read-only. It does not require trade permissions and does not submit orders.

---

## Quick start

```powershell
git clone https://github.com/nazirumar/mt5api.git
cd mt5api

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env
notepad .env
```

Set at minimum:

```dotenv
API_KEY=replace-with-a-long-random-secret
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_SERVER_UTC_OFFSET_MINUTES=0
```

Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Verify it:

```powershell
curl.exe -s http://127.0.0.1:8080/api/v1/health
curl.exe -s http://127.0.0.1:8080/docs
```

For an automated install of dependencies, `.env`, and autostart tasks:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

---

## Configuration

All settings are read from the process environment, with `.env` as a fallback. See `.env.example` for the complete template.

### Core settings

| Variable | Default | Purpose |
|---|---:|---|
| `API_HOST` | `127.0.0.1` | Bind address. Keep loopback unless you have reviewed LAN exposure. |
| `API_PORT` | `8080` | FastAPI port. |
| `API_KEY` | required in production | Secret for REST and WebSocket clients. |
| `API_KEY_HEADER` | `X-API-Key` | REST header name. |
| `LOCAL_DOMAIN` | `mt5.nazbeen.test` | Friendly hostname used by scripts and Caddy. |
| `ENABLE_CADDY` | `true` | Allows proxy discovery/autostart. |
| `CADDY_PATH` | empty | Full path to `caddy.exe`; otherwise PATH/common installs are checked. |
| `ALLOW_LAN_ACCESS` | `false` | Set only after a security review. |

### MT5 settings

| Variable | Default | Purpose |
|---|---|---|
| `MT5_PATH` | empty | Full path to `terminal64.exe` or `terminal.exe`. |
| `MT5_LOGIN` | empty | Account number for programmatic login. |
| `MT5_PASSWORD` | empty | Account password for programmatic login. |
| `MT5_SERVER` | empty | Exact broker server name. |
| `MT5_START_TERMINAL` | `true` | Allow the terminal autostart helper to launch MT5. |
| `MT5_INIT_TIMEOUT` | `60` | Initialization timeout in seconds. |
| `MT5_RECONNECT_INTERVAL` | `5` | Initial reconnect delay. |
| `MT5_MAX_RECONNECT_INTERVAL` | `60` | Maximum reconnect delay. |
| `MT5_MAX_RECONNECT_ATTEMPTS` | `10` | Reconnect attempts before giving up. |

Leave `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER` empty if the terminal is already running and logged in.

### Collector settings

| Variable | Default | Purpose |
|---|---:|---|
| `MARKET_POLL_INTERVAL` | `0.5` | Seconds between collector polls. |
| `QUOTE_STALE_MULTIPLIER` | `3` | Freshness window multiplier. |
| `WATCHED_SYMBOLS` | empty | Optional comma-separated broker symbols to preload. |
| `DEFAULT_CANDLE_LIMIT` | `500` | Default candle count. |
| `MAX_CANDLE_LIMIT` | `5000` | Maximum candle count. |
| `MAX_TICK_RECORDS` | `10000` | Maximum ticks per request. |
| `WEBSOCKET_HEARTBEAT_INTERVAL` | `30` | WebSocket ping interval. |
| `WEBSOCKET_STALE_TIMEOUT` | `90` | Stale client timeout. |

---

## Broker time and UTC

MetaTrader 5 timestamps encode the **broker terminal's server wall time**, not UTC. Without conversion, a UTC+2 broker returns timestamps two hours in the future and marks completed candles as forming.

Configure **exactly one** of:

```dotenv
# IANA timezone; recommended for brokers that observe DST.
MT5_SERVER_TIMEZONE=Europe/London

# Fixed offset in minutes east of UTC; UTC+2 = 120.
MT5_SERVER_UTC_OFFSET_MINUTES=120
```

If both are blank, the API estimates the offset from fresh MT5 ticks and rounds to 15 minutes. For reliable history, set one explicitly.

The clock is applied to:

- quote timestamps and freshness/age,
- candle epochs, ISO times, and `completed`,
- tick timestamps,
- `start` / `end` history range arguments sent to MT5.

A broker candle labelled `12:00` at UTC+2 is exported as `10:00Z`. The active clock is visible in:

```powershell
curl.exe -s http://127.0.0.1:8080/api/v1/health
curl.exe -s -H "X-API-Key: $Key" http://127.0.0.1:8080/api/v1/status
```

---

## Local domain with Caddy

The optional local hostname is `http://mt5.nazbeen.test`. The `.test` suffix is reserved for testing and will not resolve publicly.

### 1. Install Caddy

Set `CADDY_PATH` in `.env`, install Caddy system-wide, or place `caddy.exe` in `tools\`. The `tools\` directory is intentionally ignored by Git, so the binary is not committed.

```powershell
winget install --id CaddyServer.Caddy -e
```

### 2. Add the hosts entry

Run from an **elevated PowerShell**:

```powershell
$Project = "C:\path\to\mt5api"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Project\scripts\setup_domain.ps1" -Domain "mt5.nazbeen.test"
```

The helper adds `127.0.0.1 mt5.nazbeen.test`, removes duplicates, flushes DNS, and verifies resolution.

### 3. Start Caddy

The shipped `caddy\Caddyfile` is:

```caddyfile
{
	auto_https off
	admin off
}

http://mt5.nazbeen.test {
	bind 127.0.0.1
	reverse_proxy 127.0.0.1:8080
}
```

```powershell
& .\tools\caddy.exe run --config .\caddy\Caddyfile
```

You can validate the route before editing the hosts file:

```powershell
curl.exe --resolve mt5.nazbeen.test:80:127.0.0.1 -sS http://mt5.nazbeen.test/api/v1/health
```

### Local-only rule

Do **not** bind FastAPI or Caddy to `0.0.0.0`, create public DNS records, forward ports 80/8080, or terminate public TLS without a full security review.

---

## Authentication

| Client | How to send the key |
|---|---|
| REST | `X-API-Key: <key>` |
| WebSocket | `/ws/market?api_key=<key>` |
| Health/docs | Public; no key required |

The server reads the secret from `API_KEY` in `.env`. Client examples read `API_KEY` and accept `MT5_API_KEY` as a compatibility fallback.

> The WebSocket key is in the URL and can appear in proxy logs. Keep logs local and rotate the key if exposed.

---

## API reference

Base URLs:

```text
http://127.0.0.1:8080
http://mt5.nazbeen.test          # with Caddy + hosts entry
```

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/health` | Public | Liveness, MT5 state, freshness, broker clock |
| `GET` | `/api/v1/status` | Key | Component status, collector, WebSocket, config summary |
| `GET` | `/api/v1/connection` | Key | MT5 connection diagnostics |
| `GET` | `/api/v1/symbols` | Key | Broker-native symbols |
| `GET` | `/api/v1/symbols/search?q=EUR` | Key | Search symbols |
| `GET` | `/api/v1/symbols/{symbol}` | Key | Symbol details |
| `GET` | `/api/v1/quotes?symbols=EURUSD,XAUUSD` | Key | Batch quotes |
| `GET` | `/api/v1/quote/{symbol}` | Key | Single quote |
| `GET` | `/api/v1/market/sessions` | Key | Market session information |
| `GET` | `/api/v1/candles/{symbol}?timeframe=M5&limit=500` | Key | OHLC candles |
| `GET` | `/api/v1/ticks/{symbol}?start=2026-01-01T00:00:00Z` | Key | Tick history |
| `GET` | `/api/v1/account` | Key | Read-only account diagnostics |
| `WS` | `/ws/market?api_key=<key>` | Key query | Live quote stream |
| `GET` | `/docs` | Public | Interactive OpenAPI documentation |

Supported timeframe tokens include `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, and `MN1`.

### Envelopes

Success:

```json
{
  "success": true,
  "data": {},
  "meta": {}
}
```

Error:

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable explanation",
    "details": {}
  }
}
```

### WebSocket protocol

```text
ws://mt5.nazbeen.test/ws/market?api_key=YOUR_KEY
```

Subscribe:

```json
{"action":"subscribe","symbols":["EURUSD","XAUUSD"]}
```

Unsubscribe:

```json
{"action":"unsubscribe","symbols":["EURUSD"]}
```

Server message types: `welcome`, `subscribed`, `unsubscribed`, `quote`, `connection`, `heartbeat`, `pong`, `error`.

### Completed vs forming candles

Prefer `completed: true` candles for analysis and backtests. The current timeframe bar is marked `false` and changes until the interval closes.

---

## Client examples

### curl

```powershell
$Base = "http://mt5.nazbeen.test"
$Key  = $env:API_KEY
$H    = @("X-API-Key: $Key")

curl.exe -s "$Base/api/v1/health"
curl.exe -s -H $H "$Base/api/v1/quotes?symbols=EURUSD,XAUUSD"
curl.exe -s -H $H "$Base/api/v1/candles/EURUSD?timeframe=M5&limit=100"
curl.exe -s -H $H --get --data-urlencode "start=2026-01-01T00:00:00Z" "$Base/api/v1/ticks/EURUSD"
```

### Python

```powershell
$env:API_BASE_URL = "http://mt5.nazbeen.test"
$env:API_KEY = "your-long-random-secret"
python .\examples\python_client.py
```

```python
from python_client import MT5MarketDataClient

client = MT5MarketDataClient()
quote = client.quote("EURUSD")
candles = client.candles("EURUSD", timeframe="M5", limit=100)
```

### Next.js

```dotenv
NEXT_PUBLIC_MT5_API_BASE=http://mt5.nazbeen.test
NEXT_PUBLIC_API_KEY=your-long-random-secret
```

```ts
import { fetchHealth, fetchQuotes, createMarketSocket } from "./examples/nextjs_client";

const health = await fetchHealth();
const quotes = await fetchQuotes(["EURUSD", "XAUUSD"]);

const socket = createMarketSocket({
  symbols: ["EURUSD"],
  onQuote: (quote) => console.log(quote),
  onStatus: (status) => console.log("socket:", status),
  onError: (err) => console.error(err),
});
```

> `NEXT_PUBLIC_*` variables are embedded in browser JavaScript. Prefer a server-side route that keeps the API key out of the bundle. See `examples/nextjs_client.ts` for compatibility behavior.

---

## Windows autostart

`scripts\install.ps1` registers three user-level logon tasks. No administrator rights are required.

| Task | Starts | Helper behavior |
|---|---|---|
| `NazbeenMT5Terminal` | MT5 terminal from `MT5_PATH` | Starts immediately if not already running |
| `NazbeenMT5API` | Uvicorn API | Waits ~30s for the terminal, then starts without duplicates |
| `NazbeenMT5Proxy` | Caddy | Waits ~45s, starts only if Caddy is available and port 80 is free |

All tasks are configured to:

- start and continue on battery power,
- run if a logon trigger was missed,
- retry 3 times at 1-minute intervals,
- avoid duplicate instances.

```powershell
Get-ScheduledTask -TaskName "Nazbeen*"
Get-ScheduledTaskInfo -TaskName "NazbeenMT5API"

Start-ScheduledTask -TaskName "NazbeenMT5API"
Stop-ScheduledTask -TaskName "NazbeenMT5API"
```

To re-register after changing `.env` or the scripts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1
```

---

## Diagnostics

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_status.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\diagnose.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\diagnose.ps1 -Json
```

`check_status.ps1` reports Python/venv, MT5 process and connection, API listener, Caddy process/port, domain resolution, hosts entry, health, and recent log errors.

`diagnose.ps1` adds actionable PASS/WARN/FAIL rows. A live connection is claimed only when a terminal process exists **and** the API reports `terminal_connected=true`.

Other helpers:

```powershell
.\scripts\start_api.ps1
.\scripts\stop_api.ps1
.\scripts\restart_api.ps1
```

---

## Testing

Tests use a fake MT5 module; no live broker account is required.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite covers REST endpoints, WebSockets, authentication, broker-clock conversion, NumPy structured records, stale quotes, and PowerShell script parsing.

---

## Project structure

```text
mt5api/
├── app/
│   ├── api/          # REST routers and WebSocket endpoint
│   ├── core/         # Security, lifecycle, logging, helpers
│   ├── database/     # SQLite models and repositories
│   ├── mt5/          # Connection, symbols, quotes, candles, ticks, clock
│   ├── schemas/      # Pydantic response models
│   └── services/     # Collector, cache, subscriptions, monitor
├── caddy/Caddyfile
├── data/
├── examples/         # Python, Next.js, and WebSocket clients
├── logs/
├── scripts/          # Install, autostart, domain, and diagnostic helpers
├── tests/
├── .env.example
├── requirements.txt
└── README.md
```

---

## Security

- Keep the API bound to loopback.
- Keep `API_KEY` secret; rotate it if exposed.
- Do not commit `.env`, SQLite data, logs, or API keys.
- Treat browser-facing keys as public; use a server-side proxy.
- The WebSocket key appears in the URL and may be logged.
- Account data is sensitive even though the API cannot trade.
- Do not add public DNS, port forwarding, or TLS without a full review.
- Run only on a trusted Windows profile; the MT5 terminal holds broker credentials.

---

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

Optionally remove the hosts entry:

```powershell
$Project = "C:\path\to\mt5api"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Project\scripts\remove_domain.ps1" -Domain "mt5.nazbeen.test"
```

The uninstall scripts preserve `.env` and source files unless `-DeleteData` is supplied.

---

## License

Released under the [MIT License](LICENSE).
