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
- [Market context engine](#market-context-engine)
- [Multi-timeframe snapshot](#multi-timeframe-snapshot)
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

- **REST + WebSocket** market data over FastAPI, including a completed-candle stream.
- **Broker-native symbols only** — no invented tickers or synthetic prices.
- **UTC-correct timestamps** for quotes, candles, ticks, and history ranges.
- **Completed vs forming candles** reported explicitly.
- **Serialized MT5 access** through one re-entrant connection manager.
- **Shared quote/candle collector** for REST and WebSocket clients.
- **Private API key** for REST headers and WebSocket first-message auth; query-string auth remains for compatibility.
- **Zero-downtime key rotation** with `API_KEY_PREVIOUS`.
- **Request IDs and structured JSON logs** with filterable local events.
- **Health vs readiness** probes for liveness and authenticated MT5 readiness.
- **Bounded history** with range caps, rate limiting, and concurrency limits.
- **Deterministic market context** for quote, trend, regime, momentum, volatility, price position, spread, candle structure, and session state.
- **Multi-timeframe snapshot** with per-timeframe metadata, explicit failures, cross-timeframe alignment, and retrieval synchronization.
- **User-level Windows autostart** with a 5-minute watchdog and battery-safe Task Scheduler settings.
- **Optional local domain** via Caddy at `http://mt5.nazbeen.test`.
- **SQLite-backed diagnostics** and local PowerShell status tools.
- **Windows CI** on Python 3.12 and 3.13 via GitHub Actions.

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
       |          |          +--> WebSocket quotes + completed candles
       |          +-------------> Shared quote/candle collector
       +-----------------------> Serialized MT5 bridge --> logged-in terminal
                                      |
                                      +--> SQLite diagnostics
                                      +--> 5-minute watchdog repair task
```

| Component | Responsibility |
|---|---|
| FastAPI app | HTTP API, WebSocket streams, validation, request IDs, error envelopes |
| MT5 bridge | One serialized connection manager for all terminal calls |
| BrokerClock | Converts broker-server time to real UTC from fresh ticks |
| MarketCollector | Polls quotes and completed candles once for all clients |
| HistoryLimiter | Per-client rate limit and global concurrency cap |
| MarketContextEngine | Deterministic, read-only snapshots from cached/live MT5 data |
| SnapshotEngine | Bounded multi-timeframe coordination over the same Phase 18 data/analytics |
| SQLite | Local operational/diagnostic records |
| Caddy | Friendly local hostname; no public TLS or exposure |
| Watchdog | Five-minute self-healing task for API, terminal and proxy |

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

For a fresh clone, the one-command bootstrap creates the virtual environment, installs requirements, generates `.env`, configures autostart, and prints the URLs:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

To run the tests as part of bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -RunTests
```

The installer remains available when you want more control:

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
| `API_KEY_PREVIOUS` | empty | Previous secret accepted during rotation; clear after clients update. |
| `API_KEY_HEADER` | `X-API-Key` | REST header name. |
| `CORS_ORIGINS` | `http://mt5.nazbeen.test,http://127.0.0.1:8080` | Browser origins allowed by CORS; add any custom local domain here as well. |
| `LOCAL_DOMAIN` | `mt5.nazbeen.test` | Friendly hostname used by scripts and Caddy; override with `-Domain` when installing. |
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
| `MAX_CANDLE_RANGE_DAYS` | `365` | Maximum `start`/`end` span for candles. |
| `MAX_TICK_RANGE_DAYS` | `30` | Maximum `start`/`end` span for ticks. |
| `HISTORY_RATE_LIMIT_PER_MINUTE` | `30` | Per-client history request budget. |
| `HISTORY_MAX_CONCURRENT` | `2` | Concurrent MT5 history calls. |
| `CANDLE_STREAM_ENABLED` | `true` | Enables the completed-candle WebSocket stream. |
| `CANDLE_STREAM_MAX_SUBSCRIPTIONS` | `50` | Maximum candle pairs per WebSocket client message. |
| `WEBSOCKET_HEARTBEAT_INTERVAL` | `30` | WebSocket ping interval. |
| `WEBSOCKET_STALE_TIMEOUT` | `90` | Stale client timeout. |

### Market context settings

All context thresholds have defaults and are documented in `.env.example` and
`docs/PHASE_18_MARKET_CONTEXT_ENGINE.md`. Commonly adjusted values:

| Variable | Default | Purpose |
|---|---:|---|
| `CONTEXT_DEFAULT_TIMEFRAME` | `M5` | Primary timeframe when none is requested. |
| `CONTEXT_DEFAULT_LOOKBACK` | `100` | Default completed candles per timeframe. |
| `CONTEXT_MAX_LOOKBACK` | `500` | Hard ceiling for `lookback`. |
| `CONTEXT_MAX_TIMEFRAMES` | `4` | Maximum timeframes in one request. |
| `CONTEXT_MIN_CANDLES` | `10` | Minimum completed candles for usable context. |
| `CONTEXT_CANDLE_CACHE_SECONDS` | `30` | TTL for reused candle series. |
| `CONTEXT_QUOTE_VERY_STALE_MULTIPLIER` | `6` | Very-stale threshold multiplier. |
| `CONTEXT_SESSION_TIMEZONE` | `UTC` | IANA timezone for session windows; DST-aware. |
| `CONTEXT_SESSION_WINDOWS` | empty | Optional `London=7-16,New York=12-21` style override. |

### Multi-timeframe snapshot settings

All snapshot settings are additive and bounded for the low-resource Windows host. Full details are in `docs/PHASE_19_MULTI_TIMEFRAME_SNAPSHOT.md`.

| Variable | Default | Purpose |
|---|---:|---|
| `SNAPSHOT_DEFAULT_TIMEFRAMES` | `M1,M5,M15,H1,H4,D1` | Default timeframe list. |
| `SNAPSHOT_MAX_TIMEFRAMES` | `7` | Maximum timeframes in one request. |
| `SNAPSHOT_DEFAULT_LIMIT` | `100` | Default candles requested per timeframe. |
| `SNAPSHOT_MAX_LIMIT` | `500` | Hard ceiling for `limit`. |
| `SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME` | `100` | Maximum candles returned per timeframe. |
| `SNAPSHOT_SYNC_TIGHT_SECONDS` | `2` | Tight retrieval tolerance. |
| `SNAPSHOT_ACCEPTABLE_SYNC_SECONDS` | `10` | Acceptable retrieval tolerance. |
| `SNAPSHOT_MIN_ALIGNED_TIMEFRAMES` | `2` | Minimum analyzable timeframes for alignment. |
| `SNAPSHOT_ALIGNMENT_DOMINANT_RATIO` | `0.6` | Dominant-direction threshold for descriptive alignment. |

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

If both are blank, the API estimates the offset only from **fresh** MT5 ticks and rounds to 15 minutes. Historical samples are deliberately ignored because their age is not a valid clock reference. For reliable history, set one explicitly.

The clock is applied to:

- quote timestamps and freshness/age,
- candle epochs, ISO times, and `completed`,
- tick timestamps,
- `start` / `end` history range arguments sent to MT5.

A broker candle labelled `12:00` at UTC+2 is exported as `10:00Z`. The active clock is visible in:

```powershell
curl.exe -s http://127.0.0.1:8080/api/v1/health
curl.exe -s -H "X-API-Key: $Key" http://127.0.0.1:8080/api/v1/ready
curl.exe -s -H "X-API-Key: $Key" http://127.0.0.1:8080/api/v1/status
```

---

## Local domain with Caddy

The optional local hostname is `http://mt5.nazbeen.test`. The `.test` suffix is reserved for testing and will not resolve publicly.

To override it, set `LOCAL_DOMAIN` in `.env` before installing, or pass `-Domain your-host.test` to `install.ps1` / `bootstrap.ps1`. The installer regenerates `caddy\Caddyfile` from the resolved domain (still loopback-only). For browser clients, also add `http://<your-domain>` to `CORS_ORIGINS` in `.env` and restart the API: CORS is a separate allow-list and is **not** derived from `LOCAL_DOMAIN`.

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
| WebSocket (preferred) | First frame: `{"action":"auth","api_key":"<key>"}` |
| WebSocket (compatible) | `/ws/market?api_key=<key>` or configured header |
| Health, status, docs | Public; no key required |
| Ready, events | `X-API-Key: <key>` |

The server reads the secret from `API_KEY` in `.env`. During rotation, set `API_KEY_PREVIOUS` to the old secret so both keys work; clear it once every client has moved to the new key. Client examples read `API_KEY` and accept `MT5_API_KEY` as a compatibility fallback.

> First-message WebSocket auth keeps the key out of URLs and proxy logs. The query-string form remains supported only for older clients; rotate the key if it may have been logged.

---

## API reference

Base URLs:

```text
http://127.0.0.1:8080
http://mt5.nazbeen.test          # with Caddy + hosts entry
```

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/v1/health` | Public | Liveness, MT5 state, read-time freshness, broker clock |
| `GET` | `/api/v1/ready` | Key | Authenticated MT5 readiness and component detail |
| `GET` | `/api/v1/status` | Public | Component status, collector, WebSocket, config summary |
| `GET` | `/api/v1/events` | Key | Filterable recent diagnostic events |
| `GET` | `/api/v1/connection` | Key | MT5 connection diagnostics |
| `GET` | `/api/v1/symbols` | Key | Broker-native symbols |
| `GET` | `/api/v1/symbols/search?q=EUR` | Key | Search symbols |
| `GET` | `/api/v1/symbols/{symbol}` | Key | Symbol details |
| `GET` | `/api/v1/quotes?symbols=EURUSD,XAUUSD` | Key | Batch quotes |
| `GET` | `/api/v1/quote/{symbol}` | Key | Single quote |
| `GET` | `/api/v1/market/sessions` | Key | Market session information |
| `GET` | `/api/v1/market/context/{symbol}?timeframe=M5&lookback=100` | Key | Deterministic market context snapshot |
| `GET` | `/api/v1/market/snapshot/{symbol}?timeframes=M15,H1,H4,D1&limit=100` | Key | Bounded multi-timeframe snapshot |
| `GET` | `/api/v1/candles/{symbol}?timeframe=M5&limit=500` | Key | OHLC candles |
| `GET` | `/api/v1/ticks/{symbol}?start=2026-01-01T00:00:00Z` | Key | Tick history |
| `GET` | `/api/v1/account` | Key | Read-only account diagnostics |
| `WS` | `/ws/market` | First message | Live quotes and completed candles |
| `GET` | `/docs` | Public | Interactive OpenAPI documentation |

Supported timeframe tokens include `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, and `MN1`. History requests are bounded by `MAX_CANDLE_RANGE_DAYS`, `MAX_TICK_RANGE_DAYS`, `HISTORY_RATE_LIMIT_PER_MINUTE` (HTTP 429), and `HISTORY_MAX_CONCURRENT`.

### Envelopes

Success:

```json
{
  "success": true,
  "data": {},
  "meta": { "request_id": "..." }
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
  },
  "meta": { "request_id": "..." }
}
```

Every response includes an `X-Request-ID` header. Send your own `X-Request-ID` to correlate client and structured server logs, or let the API generate one. Events can be filtered with `/api/v1/events?request_id=<id>`.

### WebSocket protocol

```text
ws://mt5.nazbeen.test/ws/market
```

Authenticate as the first frame:

```json
{"action":"auth","api_key":"YOUR_KEY"}
```

Subscribe to quotes:

```json
{"action":"subscribe","symbols":["EURUSD","XAUUSD"]}
```

Subscribe to completed candles (one message per interval close):

```json
{"action":"subscribe_candles","candles":[{"symbol":"EURUSD","timeframe":"M1"}]}
```

Unsubscribe:

```json
{"action":"unsubscribe","symbols":["EURUSD"]}
{"action":"unsubscribe_candles","candles":[{"symbol":"EURUSD","timeframe":"M1"}]}
```

Server message types: `auth_required`, `authenticated`, `welcome`, `subscribed`, `subscribed_candles`, `unsubscribed`, `quote`, `candle`, `connection`, `heartbeat`, `pong`, `error`.

### Completed vs forming candles

Prefer `completed: true` candles for analysis and backtests. The current timeframe bar is marked `false` and changes until the interval closes.

### Market context engine

`GET /api/v1/market/context/{symbol}` returns a deterministic, read-only
snapshot containing quote context, trend, regime, momentum, volatility, price
position, spread intelligence, candle structure, session context, and a
0-100 data-quality score. All calculations use completed candles and real MT5
quotes; no model, signal, order, or fabricated value is involved.

```text
GET /api/v1/market/context/XAUUSDm?timeframe=M5&lookback=100
GET /api/v1/market/context/EURUSDm?timeframes=M5,M15,H1&include_candles=true
```

Classifications are descriptive measurements, not buy/sell signals or outcome
predictions. Session hours are approximate and not broker-verified. Full
methodology, thresholds, response shape, limitations, and troubleshooting are
in `docs/PHASE_18_MARKET_CONTEXT_ENGINE.md`.

### Multi-timeframe snapshot

`GET /api/v1/market/snapshot/{symbol}` assembles one broker-native symbol across
several MT5 timeframes in one bounded response. It reuses the Phase 18 engine
for every per-timeframe context and adds explicit metadata, per-timeframe
errors, cross-timeframe alignment, retrieval synchronization, and snapshot-level
completeness. Defaults and hard caps keep the request lightweight.

```text
GET /api/v1/market/snapshot/EURUSDm?timeframes=M1,M5,H1&limit=100
GET /api/v1/market/snapshot/XAUUSDm?timeframes=M15,H1,H4,D1&include_candles=false
```

`snapshot_status` is `COMPLETE`, `PARTIAL`, or `UNAVAILABLE`. A failed timeframe
is reported in `timeframes.<tf>.error` without failing the whole request. Candle
close-time differences are reported separately from retrieval synchronization;
the response never claims simultaneous fetches. Alignment counts describe
agreement among Phase 18 trend classifications and are not signals,
probabilities, or forecasts. Full methodology, configuration, examples, and
troubleshooting are in `docs/PHASE_19_MULTI_TIMEFRAME_SNAPSHOT.md`.

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
curl.exe -s -H $H --get --data-urlencode "timeframes=M15,H1,H4,D1" --data-urlencode "limit=100" "$Base/api/v1/market/snapshot/XAUUSDm"
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

snapshot = client.market_snapshot(
    "XAUUSDm", timeframes="M15,H1,H4,D1", limit=100
)
print(snapshot["snapshot_status"], snapshot["alignment"]["classification"])
```

### Next.js

Use a server-side Route Handler. The API key must stay in the server environment; `NEXT_PUBLIC_*` values are embedded in browser JavaScript.

```dotenv
# Server-only secrets (do not prefix with NEXT_PUBLIC_).
MT5_API_BASE_URL=http://mt5.nazbeen.test
API_KEY=your-long-random-secret
```

```ts
// app/api/market/route.ts
import {
  fetchHealth,
  fetchQuotes,
  fetchMarketContext,
  fetchMarketSnapshot,
} from "@/examples/nextjs_client";

export async function GET() {
  const health = await fetchHealth();
  const quotes = await fetchQuotes(["EURUSD", "XAUUSD"]);
  const context = await fetchMarketContext("XAUUSDm", {
    timeframes: "M5,M15,H1",
  });
  const snapshot = await fetchMarketSnapshot("XAUUSDm", {
    timeframes: "M15,H1,H4,D1",
    limit: 100,
  });
  return Response.json({ health, quotes, context, snapshot });
}
```

The bundled WebSocket helper uses first-message auth and is also intended for server-side use. Never put an API key in `NEXT_PUBLIC_*` variables or a browser bundle.

---

## Windows autostart

`scripts\install.ps1` registers three user-level logon tasks plus one recurring watchdog task. No administrator rights are required.

| Task | Starts | Helper behavior |
|---|---|---|
| `NazbeenMT5Terminal` | MT5 terminal from `MT5_PATH` | Starts immediately if not already running |
| `NazbeenMT5API` | Uvicorn API | Waits ~30s, then up to 120s for the terminal, and starts without duplicates |
| `NazbeenMT5Proxy` | Caddy | Waits ~45s, starts only if Caddy is available and port 80 is free |
| `NazbeenMT5Watchdog` | Self-healing check | Every 5 minutes; starts missing API/terminal/proxy, restarts unhealthy API with a cooldown |

All tasks are configured to:

- start and continue on battery power,
- run if a logon trigger was missed,
- retry 3 times at 1-minute intervals,
- avoid duplicate instances.

```powershell
Get-ScheduledTask -TaskName "Nazbeen*"
Get-ScheduledTaskInfo -TaskName "NazbeenMT5API"

Start-ScheduledTask -TaskName "NazbeenMT5API"

# Stopping the scheduled task does not terminate the running API process.
powershell -ExecutionPolicy Bypass -File .\scripts\stop_api.ps1

# Run the self-healing check immediately.
powershell -ExecutionPolicy Bypass -File .\scripts\watchdog.ps1
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

`check_status.ps1` reports Python/venv, MT5 process and connection, API listener, Caddy process/port, domain resolution, hosts entry, health, and recent log errors. The authenticated `/api/v1/ready` endpoint adds component-level readiness and collector statistics.

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

The suite covers REST endpoints, WebSockets, first-message auth, candle streams, authentication and rotation, request IDs, broker-clock conversion, history bounds, deterministic context analytics, multi-timeframe alignment/synchronization, mocked-MT5 snapshot scenarios, PowerShell script parsing, and CI/bootstrap metadata. GitHub Actions runs the same suite on Windows with Python 3.12 and 3.13. Tests use mocked MT5 only; they do not claim live broker verification.

---

## Project structure

```text
mt5api/
├── app/
│   ├── api/          # REST routers and WebSocket endpoints
│   ├── context/      # Phase 18 deterministic analytics and context engine
│   ├── core/         # Security, lifecycle, request context, structured logging
│   ├── database/     # SQLite models and repositories
│   ├── mt5/          # Connection, symbols, quotes, candles, ticks, clock
│   ├── schemas/      # Pydantic response models
│   ├── services/     # Collector, caches, subscriptions, limiter, monitor
│   └── snapshot/     # Phase 19 multi-timeframe coordination/alignment
├── .github/workflows/ci.yml
├── caddy/Caddyfile
├── data/
├── docs/             # Phase 17/18/19 audits, plans, and methodology
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
- Keep `API_KEY` secret; rotate it with `API_KEY_PREVIOUS` and clear the old value when clients have updated.
- Do not commit `.env`, SQLite data, logs, or API keys.
- Treat browser-facing keys as public; use a server-side proxy.
- Prefer WebSocket first-message auth; query-string keys may appear in proxy logs.
- History endpoints are rate-limited and range-bounded; keep the configured caps unless you have reviewed the load.
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

The uninstall scripts preserve `.env` and source files unless `-DeleteData` is supplied. `uninstall_autostart.ps1` also removes `NazbeenMT5Watchdog` unless `-KeepWatchdog` is passed.

---

## License

Released under the [MIT License](LICENSE).
