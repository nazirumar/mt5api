# Nazbeen MT5 Market Data API

A local, read-only market data API for MetaTrader 5 (MT5). It exposes REST and WebSocket endpoints backed by a logged-in MT5 terminal, with a shared quote collector, local diagnostics, and an optional Caddy reverse proxy for a friendly local domain.

> **Current machine status:** Python **3.13.14** and MT5 at **`C:\Program Files\MetaTrader 5\terminal64.exe`** are present. A real terminal is logged in to **MetaQuotes-Demo**, and the API has been verified live on `http://127.0.0.1:8080` with correct UTC quotes, candles, and tick history. Caddy is bundled at `tools\caddy.exe` and configured for `http://mt5.nazbeen.test`, but browser access by that hostname still needs an elevated hosts-file entry. No prices are invented or simulated.

---

## Overview / architecture

```text
Browser / curl / Python / Next.js
              |
              v
   http://mt5.nazbeen.test  (Caddy, optional, local-only)
              |
              v
        FastAPI app  (REST + WebSocket)
              |
              +--> serialized MT5 access thread  --> MT5 terminal / broker
              |
              +--> shared quote collector --------> /quote, /quotes, WS quotes
              |
              +--> SQLite diagnostics ------------> status/latency/error history
```

Key design points:

- **FastAPI** provides all HTTP endpoints and the `/ws/market` WebSocket.
- **Serialized MT5 access thread**: MT5 bridge calls are funnelled through one dedicated thread/queue because the MT5 Python integration is not safe for concurrent multi-threaded access. This keeps calls ordered and predictable.
- **Shared quote collector**: one collector maintains the subscribed symbol set used by REST quote endpoints and WebSocket broadcasts, instead of each client polling the terminal independently.
- **SQLite diagnostics**: local diagnostic data (connection state, request errors, latency/recovery history where enabled) is written to SQLite. Treat this database as local operational data.
- **Caddy**: optional local reverse proxy that maps `http://mt5.nazbeen.test` to the FastAPI port. It is for local convenience only.
- **Read-only**: the project retrieves market data and account/status information. It does **not** place, modify, or close trades.
- **No invented prices**: if MT5 is unavailable, the API reports the problem. It never fabricates quotes or candles.

---

## Prerequisites

- **Windows** (the MT5 Python bridge and desktop terminal are Windows-oriented).
- **Python 3.12+**. This machine has Python 3.13, which satisfies the requirement.
- **MetaTrader 5 terminal** installed and logged in to the intended broker account/server.
- **Caddy** (optional) if you want `http://mt5.nazbeen.test` instead of `http://127.0.0.1:8080`.
- Caddy is bundled in this project as `tools\caddy.exe` (release v2.11.9, SHA-512 verified). A system-wide install also works.
- Administrator rights are needed only for hosts-file edits. User-level scheduled tasks do not require administrator rights.
- An API key value you generate and keep out of source control.

---

## Installation

### Automated install

Run from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Review `scripts/install.ps1` before running it. Depending on the project version, it may create/validate the virtual environment, install dependencies, prepare `.env`, create Caddy configuration, and register Windows autostart tasks.

### Manual install

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3. Create configuration
Copy-Item .env.example .env
notepad .env

# 4. Start MT5: set MT5_PATH to C:\Program Files\MetaTrader 5\terminal64.exe in .env, or open and log in to the terminal manually.

# 5. Start the API from the project root with the activated venv python.
#    Adjust the module name if your checkout exposes `app` elsewhere.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080

# 6. Optional: start the bundled Caddy with caddy/Caddyfile
& .\tools\caddy.exe run --config .\caddy\Caddyfile
```

Health check:

```powershell
curl.exe -s http://127.0.0.1:8080/api/v1/health
```

---

## MT5 credential configuration (`.env`)

Create/edit `.env` in the project root:

```dotenv
# Full path to terminal64.exe. REQUIRED if the terminal is not started manually.
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# Fill these only if you want the service to log in programmatically.
# If MT5 is already opened and logged in, leave them empty.
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=

# Server-side API key used by clients via X-API-Key / ?api_key=.
API_KEY=replace-with-a-long-random-secret

# Optional local proxy binary. Leave blank to use PATH/common installs.
CADDY_PATH=C:\path\to\tools\caddy.exe

# Broker clock: set exactly ONE of these. The fixed offset is in minutes
# east of UTC, so UTC+2 is 120. Leave both blank for best-effort detection.
MT5_SERVER_TIMEZONE=
MT5_SERVER_UTC_OFFSET_MINUTES=
```

Notes:

- `MT5_PATH` must point to the actual `terminal64.exe`. On this machine it is `C:\Program Files\MetaTrader 5\terminal64.exe` and a live terminal is logged in to MetaQuotes-Demo.
- `MT5_LOGIN` is the broker account number.
- `MT5_PASSWORD` is the broker account password.
- `MT5_SERVER` is the exact broker server name shown in MT5.
- If `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER` are empty, the service assumes the terminal is already running and authenticated.
- `MT5_SERVER_TIMEZONE` accepts an IANA name such as `Etc/GMT-2` or `Europe/London`. `MT5_SERVER_UTC_OFFSET_MINUTES` accepts a fixed east-of-UTC offset such as `120`. Setting both is a configuration error.
- This broker observes UTC+2, so this machine uses `MT5_SERVER_UTC_OFFSET_MINUTES=120`.
- Do not commit `.env`. Keep credentials and the API key local.

---

## Local domain setup

The default local hostname is `mt5.nazbeen.test`. The `.test` suffix is reserved for testing and will not resolve publicly.

### 1. Add the hosts entry

Open an **elevated** Notepad (Run as administrator):

```powershell
notepad C:\Windows\System32\drivers\etc\hosts
```

Add:

```text
127.0.0.1 mt5.nazbeen.test
```

The project also provides an idempotent helper. Because this session is not elevated, run this exact command in an **elevated PowerShell** window:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Nazbeen\Documents\Nazbeen MT5 Market Data API\scripts\setup_domain.ps1" -Domain "mt5.nazbeen.test"
```

It writes `127.0.0.1 mt5.nazbeen.test`, removes duplicate mappings, flushes DNS, and verifies resolution. Until it is run, `curl.exe --resolve` and the diagnostics script can still validate the Caddy route.

Flush DNS manually only if you edit the hosts file by hand:

```powershell
ipconfig /flushdns
```

### 2. caddy/Caddyfile

Example `caddy/Caddyfile`:

The shipped `caddy/Caddyfile` is:

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

Start the bundled Caddy:

```powershell
& .\tools\caddy.exe run --config .\caddy\Caddyfile
```

You can verify the hostname route before editing the hosts file:

```powershell
curl.exe --resolve mt5.nazbeen.test:80:127.0.0.1 -sS http://mt5.nazbeen.test/api/v1/health
```

### 3. Local-only warning

This setup uses **`http://` endpoints on loopback only**. It is **local-only and NOT public**.

- Do not bind FastAPI or Caddy to `0.0.0.0`.
- Do not create public DNS records for this name.
- Do not forward ports 80/8080 through your router.
- Do not terminate TLS for public access without a full security review.
- Keep Windows Firewall restrictions in place.

Public URL example, for clarity: `http://mt5.nazbeen.test/api/v1/health`.

---

## Windows autostart tasks

The installer registers logon tasks named:

| Task name | Starts | Typical delay | Recovery behavior |
|---|---|---:|---|
| `NazbeenMT5Terminal` | MT5 terminal (`terminal64.exe`) using `MT5_PATH` (if configured) | ~30 seconds after logon | Restart on failure, up to 3 attempts at ~1 minute intervals |
| `NazbeenMT5API` | FastAPI service | ~60 seconds after logon | Restart on failure, up to 3 attempts at ~1 minute intervals |
| `NazbeenMT5Proxy` | Caddy reverse proxy | ~90 seconds after logon | Restart on failure, up to 3 attempts at ~1 minute intervals |

The installer uses ~30s for the terminal, ~30s for the API, and ~45s for Caddy. On this machine `NazbeenMT5Terminal` and `NazbeenMT5API` are installed and Ready. `NazbeenMT5Proxy` was initially skipped because Caddy was missing; with `tools\caddy.exe` and `CADDY_PATH` configured, re-run `powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1` to register it.

Useful commands:

```powershell
Get-ScheduledTask -TaskName "Nazbeen*"
Get-ScheduledTaskInfo -TaskName "NazbeenMT5API"

Start-ScheduledTask -TaskName "NazbeenMT5API"
Stop-ScheduledTask -TaskName "NazbeenMT5API"
Disable-ScheduledTask -TaskName "NazbeenMT5Proxy"
```

If you installed manually, create equivalent tasks with logon triggers, the delays above, and "Restart the task on failure".

---

## Authentication

- The server keeps the secret in `API_KEY` (not `MT5_API_KEY`).
- REST clients send it in the `X-API-Key` header.
- The bundled client examples read `API_KEY` and accept `MT5_API_KEY` only as a compatibility fallback.
- `GET /api/v1/health` is **public** and needs no key.
- All other REST endpoints require the key.
- WebSocket clients pass it in the query string: `/ws/market?api_key=...`.

Example:

```http
X-API-Key: your-long-random-secret
```

Keep the key out of source code, screenshots, logs, and browser bundles. `NEXT_PUBLIC_*` variables are visible to browsers; use them only for local development or proxy the API through a server-side route.

---

## API conventions

### Base URL

- Local domain: `http://mt5.nazbeen.test`
- REST prefix: `/api/v1`
- WebSocket: `/ws/market?api_key=...`

### Standard success envelope

```json
{
  "success": true,
  "data": { "...": "endpoint-specific data returned from MT5 or local state" },
  "meta": { "...": "request metadata such as timestamps or counts" }
}
```

### Error envelope

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable explanation",
    "details": { "...": "optional validation or diagnostic details" }
  }
}
```

### Timeframes

Supported MT5-style timeframe tokens are from **M1 through MN1**, for example:

```text
M1, M2, M3, M4, M5, M6, M10, M12, M15, M20, M30,
H1, H2, H3, H4, H6, H8, H12,
D1, W1, MN1
```

### Timestamps

All timestamps are **UTC**. Use ISO-8601 values with `Z`, or epoch values as returned by the endpoint. Convert to local time only at the presentation layer.

MT5 stores broker-server wall time in its timestamp fields. This API applies `BrokerClock` so quotes, candles, ticks, and `start`/`end` history ranges are converted consistently:

- Configure `MT5_SERVER_TIMEZONE` or `MT5_SERVER_UTC_OFFSET_MINUTES`, never both.
- If neither is configured, the offset is estimated from fresh MT5 timestamps and rounded to 15 minutes.
- `GET /api/v1/status` and `GET /api/v1/health` report the active `broker_clock` settings.
- On this machine the offset is fixed at `120` minutes because MetaQuotes-Demo reports UTC+2.

Example: a broker candle labelled `12:00` at UTC+2 is exported as `10:00Z`.

### Completed vs forming candles

- Prefer completed candles for analysis, backtests, and indicators.
- The current timeframe bar is **forming** and changes until its interval closes.
- If an endpoint marks a candle as forming/partial, do not treat it as final.

### Stale quote detection

Quotes come from a shared collector and reflect the last data seen from MT5. Always check freshness:

1. Read the quote timestamp/age from `data`/`meta`.
2. Compare it with the current UTC time.
3. If it is older than your threshold (or marked stale), treat it as stale and do not use it for decisions.
4. Check `/api/v1/connection` or `/api/v1/status` when quotes stop updating.

### Scope limits

- Read-only market data; **no trade execution**.
- No synthetic, cached-as-live, or invented prices.
- Account endpoint exposes broker account state for diagnostics, not trading.

---

## Endpoints

| Method | Endpoint | Auth | Description | Example |
|---|---|---|---|---|
| `GET` | `/api/v1/health` | Public | Process liveness/health | `/api/v1/health` |
| `GET` | `/api/v1/status` | API key | Service and collector status | `/api/v1/status` |
| `GET` | `/api/v1/connection` | API key | MT5 terminal/connection diagnostics | `/api/v1/connection` |
| `GET` | `/api/v1/symbols` | API key | Available MT5 symbols | `/api/v1/symbols` |
| `GET` | `/api/v1/symbols/search?q=EUR` | API key | Search symbols | `/api/v1/symbols/search?q=EUR` |
| `GET` | `/api/v1/symbols/{symbol}` | API key | Symbol details | `/api/v1/symbols/XAUUSD` |
| `GET` | `/api/v1/quotes?symbols=...` | API key | Batch quotes | `/api/v1/quotes?symbols=XAUUSD,EURUSD` |
| `GET` | `/api/v1/quote/{symbol}` | API key | Single quote | `/api/v1/quote/XAUUSD` |
| `GET` | `/api/v1/market/sessions` | API key | Market session information | `/api/v1/market/sessions` |
| `GET` | `/api/v1/candles/{symbol}?timeframe=M5&limit=500` | API key | OHLC candles | `/api/v1/candles/XAUUSD?timeframe=M5&limit=500` |
| `GET` | `/api/v1/ticks/{symbol}?start=...` | API key | Tick history from UTC start | `/api/v1/ticks/XAUUSD?start=2026-11-26T00:00:00Z` |
| `GET` | `/api/v1/account` | API key | Read-only account info | `/api/v1/account` |
| `WS` | `/ws/market?api_key=...` | API key query param | Live quote stream | `/ws/market?api_key=YOUR_KEY` |

### WebSocket protocol

Connect to:

```text
ws://mt5.nazbeen.test/ws/market?api_key=YOUR_KEY
```

Subscribe after connecting:

```json
{"action":"subscribe","symbols":["XAUUSD","EURUSD"]}
```

Unsubscribe:

```json
{"action":"unsubscribe","symbols":["XAUUSD","EURUSD"]}
```

Server message types: `welcome`, `subscribed`, `unsubscribed`, `quote`, `connection`, `heartbeat`, `pong`, `error`.

Quote payloads are inside `data`:

```json
{
  "type": "quote",
  "data": { "...": "quote fields from MT5 / shared collector" }
}
```

---

## curl examples

```powershell
$Base = "http://mt5.nazbeen.test"
$Key  = $env:API_KEY

# Public
curl.exe -s "$Base/api/v1/health"

# Authenticated
$Headers = @("X-API-Key: $Key")

curl.exe -s -H $Headers "$Base/api/v1/status"
curl.exe -s -H $Headers "$Base/api/v1/connection"
curl.exe -s -H $Headers "$Base/api/v1/symbols"
curl.exe -s -H $Headers --get --data-urlencode "q=EUR" "$Base/api/v1/symbols/search"
curl.exe -s -H $Headers "$Base/api/v1/symbols/XAUUSD"
curl.exe -s -H $Headers --get --data-urlencode "symbols=XAUUSD,EURUSD" "$Base/api/v1/quotes"
curl.exe -s -H $Headers "$Base/api/v1/quote/XAUUSD"
curl.exe -s -H $Headers "$Base/api/v1/market/sessions"
curl.exe -s -H $Headers --get --data-urlencode "timeframe=M5" --data-urlencode "limit=500" "$Base/api/v1/candles/XAUUSD"
curl.exe -s -H $Headers --get --data-urlencode "start=2026-11-26T00:00:00Z" "$Base/api/v1/ticks/XAUUSD"
curl.exe -s -H $Headers "$Base/api/v1/account"
```

---

## Python usage

Set the client environment first (`MT5_API_KEY` is accepted as a compatibility fallback):

```powershell
$env:API_BASE_URL = "http://mt5.nazbeen.test"
$env:API_KEY = "your-long-random-secret"
python .\examples\python_client.py
```

From your own code:

```python
from python_client import MT5MarketDataClient

client = MT5MarketDataClient()
quote = client.quote("XAUUSD")
candles = client.candles("XAUUSD", timeframe="M5", limit=500)
```

See `examples/python_client.py` for a standalone client with an httpx/requests fallback.

---

## Next.js usage

Set build/local environment variables:

```dotenv
NEXT_PUBLIC_MT5_API_BASE=http://mt5.nazbeen.test
NEXT_PUBLIC_API_KEY=your-long-random-secret
```

Remember: `NEXT_PUBLIC_*` values are embedded in browser JavaScript. Use them only for local development, or proxy through your own server-side route. `NEXT_PUBLIC_MT5_API_KEY` is accepted only as a compatibility fallback.

```ts
import { fetchHealth, fetchQuotes, createMarketSocket } from "./examples/nextjs_client";

const health = await fetchHealth();
const quotes = await fetchQuotes(["XAUUSD", "EURUSD"]);

const socket = createMarketSocket({
  symbols: ["XAUUSD", "EURUSD"],
  onQuote: (quote) => console.log(quote),
  onStatus: (status) => console.log("socket:", status),
  onError: (err) => console.error(err),
});

// later
socket.close();
```

See `examples/nextjs_client.ts` for typed REST helpers and an auto-reconnecting browser WebSocket client.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl` connection refused | API not running or wrong port | Start `NazbeenMT5API`; verify `http://127.0.0.1:8080/api/v1/health` |
| `mt5.nazbeen.test` does not resolve | Missing hosts entry or stale DNS | Add `127.0.0.1 mt5.nazbeen.test`; run `ipconfig /flushdns`; run as admin |
| 401 / unauthorized | Missing or wrong key | Send `X-API-Key` (REST) or `?api_key=` (WS); check `API_KEY` |
| Health works but quotes fail | MT5 terminal not connected | Start/log in to MT5; check `/api/v1/connection` and `/api/v1/status` |
| `terminal64.exe` path is wrong | Bad `MT5_PATH` | Use `C:\Program Files\MetaTrader 5\terminal64.exe` in `.env`, or start MT5 manually |
| Empty symbol list | Terminal not logged in or broker symbols unavailable | Log in, wait for terminal sync, retry `/api/v1/symbols` |
| Symbol not found | Wrong broker symbol name | Search with `/api/v1/symbols/search?q=EUR`; broker suffixes vary (`m`, `pro`, etc.) |
| Invalid timeframe | Unsupported timeframe token | Use MT5 tokens such as `M1`, `M5`, `H1`, `D1`, `W1`, `MN1` |
| Stale quotes | Collector not receiving updates | Check connection, terminal internet/broker feed, and quote age in `meta` |
| Timestamps are in the future | Broker clock not configured | Set `MT5_SERVER_TIMEZONE` or `MT5_SERVER_UTC_OFFSET_MINUTES`; check `broker_clock` in `/status` |
| Current candle always forming | Broker epoch treated as UTC | Configure the broker clock; restart the API |
| Caddy executable not found | `CADDY_PATH` unset and no system install | Set `CADDY_PATH` to `tools\caddy.exe`, or use the bundled path already discovered by scripts |
| Caddy 502 | API port mismatch | Ensure Caddy proxies to `127.0.0.1:8080` and API is listening |
| WebSocket closes immediately | Missing/invalid key, proxy not forwarding upgrade | Check query param and `reverse_proxy /ws/market*` rule |
| High memory/CPU | Too many symbols, huge tick/candle pulls | Reduce `limit`, narrow time ranges, subscribe only to needed symbols |
| Scheduled task stops | Terminal/API dependency not ready | Verify delays and recovery settings; start tasks manually once to diagnose |

---

## Uninstall

Stop and remove the scheduled tasks:

```powershell
Stop-ScheduledTask -TaskName "NazbeenMT5Proxy" -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName "NazbeenMT5API" -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName "NazbeenMT5Terminal" -ErrorAction SilentlyContinue

Unregister-ScheduledTask -TaskName "NazbeenMT5Proxy","NazbeenMT5API","NazbeenMT5Terminal" -Confirm:$false
```

Then:

1. Remove the `127.0.0.1 mt5.nazbeen.test` line from the hosts file (admin required).
2. Stop/uninstall Caddy if it was installed only for this project.
3. Delete or archive `.env` and the local SQLite diagnostics database if no longer needed.
4. Delete `.venv` and the project checkout if desired.
5. Do not delete the MT5 terminal or broker account unless that is explicitly intended.

---

## Security considerations

- Keep the service bound to loopback. Do not expose it publicly.
- Keep `API_KEY` secret; rotate it if exposed.
- Do not commit `.env`, SQLite diagnostics, or API keys.
- Treat `NEXT_PUBLIC_API_KEY` as public; restrict it to local development or use a server-side proxy.
- The WebSocket key is in the URL, so it can appear in proxy/access logs. Keep logs local and rotate keys if needed.
- This API is read-only and does not trade, but account data is still sensitive.
- Do not add public TLS/forwarding without authentication, rate limiting, logging review, and a security review.
- Run the service only on a trusted machine/profile; the MT5 terminal is desktop software with broker credentials.

---

## Resource notes for i3 / 8 GB machines

- Run **one** FastAPI process and **one** MT5 terminal instance.
- Do not use multiple Uvicorn workers; MT5 access is intentionally serialized through one thread.
- Keep `limit` reasonable (for example, 500 candles) and narrow tick ranges.
- Subscribe WebSocket clients only to symbols actually needed.
- Avoid keeping many historical tick exports in memory.
- SQLite diagnostics should remain on a local disk; avoid network drives.
- If the machine is constrained, stop the Caddy proxy and use `http://127.0.0.1:8080` directly.

---

## Final note

This documentation describes local deployment of a read-only MT5 market data service. Live data is only available when a real MT5 terminal is installed, configured, connected, and logged in. On this machine that condition is met: MetaQuotes-Demo is connected, the test suite passes, and REST responses were verified against live MT5 data. The remaining machine-specific step is the elevated hosts-file entry for `mt5.nazbeen.test`; until then use `http://127.0.0.1:8080` or `curl.exe --resolve` for the friendly hostname.
