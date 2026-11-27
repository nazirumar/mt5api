# Phase 17 Deep Technical Audit and Live Validation

**Repository:** `nazirumar/mt5api`  
**Branch:** `main`  
**Audited baseline:** `83a1db84297d95c5975a814bfe57e0ce81b5c947`  
**Audit date:** 2026-11-27 (UTC)  
**Method:** static source review of the checked-out tree, mocked/unit tests, and non-trading live HTTP/WebSocket probes against the already-running local API. No trades, orders, or balance-changing operations were performed. No live credentials are reproduced in this report.

## 1. Executive summary

The existing application is a coherent single-process FastAPI service that exposes read-only MetaTrader 5 market data over REST and WebSocket. Its core design choices—one MT5 connection owner, an `RLock` around synchronous MT5 calls, one shared market collector, bounded client queues, and SQLite used only for diagnostics—are sound and were confirmed in source and live traffic.

Live validation succeeded for health, readiness, docs, symbol discovery, quotes, M1–D1 candles, tick history, and WebSocket quote/candle streaming. No fabricated values were used; every live table below comes from the running API.

Phase 17 did find real defects. The most consequential were in broker-time auto-detection (negative UTC offsets were rejected, which could corrupt timestamps and candle completion), double initialization of MT5, misleading `"0: OK"` error reporting, duplicate completed-candle emissions, synchronous work on the event loop, public status information disclosure, WebSocket lifecycle/auth gaps, local-domain override drift, and watchdog restart-loop protection missing on one branch. Confirmed high/medium items were fixed with regression tests; lower-severity and behavioral risks are documented with priorities.

The previously repaired watchdog failover was re-verified: after stopping the API, the watchdog returned `Action="api-started"`, `ApiRestarted=true`, `Error=null`, and clean JSON. GitHub Actions run `46065541388` for baseline commit `83a1db8` completed with `conclusion=success`.

## 2. Architecture

The application factory in `app/main.py` builds one `AppContext` at startup and stores it on `app.state.context`. That context owns settings, runtime state, the single `MT5ConnectionManager`, MT5 domain services, caches, subscription registry, history limiter, shared collector, connection monitor, and SQLite session factory. Routers are mounted under `/api/v1`; `/ws/market` is mounted separately.

MT5 access is isolated in `app/mt5/`. Every MetaTrader5 operation is intended to go through `MT5ConnectionManager.call()`, which holds a re-entrant lock and uses `asyncio.to_thread` at async boundaries. REST services are thin adapters; response shaping and normalization live in `app/services/data_validator.py` and `app/schemas/`. Persistence is diagnostic-only SQLite in `app/database/`.

The deployment topology is deliberately local: Uvicorn binds `127.0.0.1:8080`, Caddy binds `127.0.0.1:80` for `http://mt5.nazbeen.test`, and Windows Task Scheduler runs terminal, API, proxy, and watchdog helpers in the interactive user session.

## 3. Components

| Layer | Files | Confirmed role |
|---|---|---|
| App/lifecycle | `app/main.py`, `app/dependencies.py` | Factory, lifespan, middleware, exception envelopes, DI container |
| REST API | `app/api/*.py` | Health/ready/status, connection, account, symbols, quotes, candles, ticks, sessions, events |
| WebSocket | `app/api/websocket.py`, `app/services/subscription_manager.py` | Auth, subscribe/unsubscribe, shared fan-out, bounded queues |
| MT5 adapter | `app/mt5/connection.py`, `quotes.py`, `candles.py`, `ticks.py`, `symbols.py`, `account.py`, `clock.py`, `terminal.py`, `timeframes.py` | Serialized runtime ownership, broker-time conversion, terminal discovery, read-only market data |
| Services | `app/services/market_collector.py`, `connection_monitor.py`, `cache.py`, `candle_cache.py`, `history_limiter.py`, `data_validator.py` | Shared polling, reconnect, caches, guards, normalization |
| Database | `app/database/session.py`, `models.py`, `repositories.py` | `service_events`, `symbol_metadata`, `collector_stats`; no tick/candle persistence |
| Cross-cutting | `app/core/security.py`, `logging.py`, `context.py`, `exceptions.py`, `helpers.py` | Constant-time key checks, structured JSON logs, request IDs, error envelopes |
| Automation | `scripts/*.ps1`, `caddy/Caddyfile`, `.github/workflows/ci.yml` | Install/bootstrap, autostart, watchdog, domain/Caddy setup, diagnostics, CI |

## 4. Endpoints

All REST paths are prefixed `/api/v1`. Public: `/health`, `/status`, `/`, `/docs`, `/redoc`, `/openapi.json`. API-key protected: `/ready`, `/events`, `/connection`, `/account`, `/market/sessions`, `/symbols`, `/symbols/search`, `/symbols/{symbol}`, `/quotes`, `/quote/{symbol}`, `/candles/{symbol}`, `/ticks/{symbol}`. WebSocket: `/ws/market`, accepting query `api_key`, configured API-key header, or a first `auth` frame.

Live checks confirmed:

- Direct `http://127.0.0.1:8080/api/v1/health` and domain `http://mt5.nazbeen.test/api/v1/health`: `status=ok`, `terminal_connected=true`, `data_freshness=live`.
- `http://mt5.nazbeen.test/docs`: HTTP 200 with Swagger UI.
- `GET /api/v1/ready`: `ready=true`, broker clock `utc_offset_minutes=120`, `auto_detected=false`, collector running with no errors.
- Unauthenticated `/quotes`, `/ready`, `/events`, `/account`: HTTP 401, generic `{success:false,error,meta}` envelope, `WWW-Authenticate: ApiKey`.
- Wrong API key: HTTP 401. Account endpoint returns an explicit safe-field allowlist only.
- WebSocket first-message auth: `auth_required` first, `UNAUTHORIZED` before auth, then `authenticated` and `welcome` after a valid key. Query auth returns `welcome` with `auth_mode=query_or_header`. Invalid JSON and unknown actions return `INVALID_JSON` / `UNKNOWN_ACTION`. Ping/pong, quote subscribe, candle subscribe, unsubscribe, reconnect, and multiple clients all worked. A deliberately slow client did not prevent a healthy client from receiving 10 quote messages.

## 5. Dependencies

`requirements.txt` declares FastAPI, Uvicorn, MetaTrader5, SQLAlchemy, pydantic, python-dotenv, pandas, websockets, httpx, pytest, and pytest-asyncio. Static review found `pandas` unused by `app/`; `websockets` and `httpx` are used by `examples/`, not the server. There is no lockfile and dependency bounds are loose. CI passed on Python 3.12 and 3.13 for the baseline commit.

## 6. Connection lifecycle

`MT5ConnectionManager` lazily imports MetaTrader5, discovers or launches `terminal64.exe`/`terminal.exe`, optionally logs in from `.env`, and stores initialized/connected/broker/account metadata under a lock. Health snapshots refresh terminal connection state. `ConnectionMonitor` checks `is_connected()` at `MT5_RECONNECT_INTERVAL`, emits connection events through the collector, and invokes bounded exponential-backoff reconnect with single-flight protection. Shutdown stops monitor, collector, MT5, then disposes the database engine.

Live runtime at audit time: broker `MetaQuotes Ltd.`, server `MetaQuotes-Demo`, terminal `C:\Program Files\MetaTrader 5\terminal64.exe`, `reconnect_attempts=0`, collector `poll_count=1402`, `last_latency_ms=3.08`, `cache_symbols=6`.

## 7. Background tasks

Two long-running tasks are created in the lifespan unless `DISABLE_BACKGROUND=true`:

- `MarketCollector`: polls subscribed symbols (max 30) or an auto-discovered watchlist (max 6), updates `QuoteCache`, publishes quotes to WebSocket subscribers, polls completed candles, prunes stale WebSocket clients every 60 polls, and writes collector stats every 120 polls.
- `ConnectionMonitor`: detects connection transitions and drives reconnect.

Both tasks are cancelled and awaited on shutdown. The audit found and fixed synchronous SQLite writes inside these coroutines; MT5 data calls already used `asyncio.to_thread` except for health/status snapshots, which were also moved off the event loop.

## 8. Cache

`QuoteCache` uses a `threading.Lock`, stores the last quote per symbol, and recomputes freshness at read time from the quote timestamp and `MARKET_POLL_INTERVAL * QUOTE_STALE_MULTIPLIER`; stale flags are never baked into cached objects. `CandleCache` stores the latest completed candle per `(symbol, timeframe)` for WebSocket subscribe snapshots. Explicit `?symbols=` quote requests bypass the cache and call MT5 directly. WebSocket fan-out uses per-client `asyncio.Queue(maxsize=64)` with drop-oldest behavior and synthesized heartbeats on timeout. Cache hit/miss counters are not implemented; freshness and cached symbol count are exposed instead.

## 9. Database

SQLite only. Three tables exist: `service_events`, `symbol_metadata`, and `collector_stats`. Writes swallow exceptions so diagnostics never break market data delivery. `collector_stats` is pruned to a two-day cutoff; `service_events` is not pruned. `symbol_metadata` and `upsert_symbol` are currently unused. No tick or candle history is persisted, which is intentional for this read-only API.

## 10. Startup automation

Windows Task Scheduler registers `NazbeenMT5Terminal`, `NazbeenMT5API`, `NazbeenMT5Proxy`, and `NazbeenMT5Watchdog` with interactive user sessions, battery-safe flags, duplicate-instance protection, and staged delays. Helpers check ports/processes before starting anything. Caddy is validated and hidden-started only when enabled and no port-80 conflict exists. Hosts-file setup is admin-gated and verifies `mt5.nazbeen.test -> 127.0.0.1`; Caddy binds loopback with `auto_https off` and `admin off`. No firewall, port-forward, or public DNS changes are made. A reboot test of all four tasks remains the only startup item not exercised in this phase.

The watchdog failover drill passed after the prior fix: stopping the API produced `Action="api-started"`, `ApiRestarted=true`, `Error=null`, and parseable JSON. Phase 17 found one additional cooldown gap in the unreachable branch and fixed it.

## 11. Security

Confirmed controls: constant-time API-key comparison, current/previous key rotation, no key/password in normal responses or structured HTTP logs, generic 401 envelopes, sanitized account fields, request validation/caps, explicit CORS origins, security headers, consistent error envelopes, and no trading/order calls. A `symbol_select` watchlist mutation is the only terminal-side write and is required for history calls.

Live probes confirmed 401 for four protected endpoints without a key and for a wrong key; docs are public; allowed CORS origins receive `Access-Control-allow-origin`, while a disallowed preflight receives no allow-origin header. WebSocket auth-before-data, invalid-key close 1008, invalid JSON/action errors, and slow-consumer isolation were verified live. Query-string `api_key` remains accepted for compatibility; it is documented as less safe because proxy/access logs may retain it.

Public `/api/v1/status` was confirmed to disclose internal manager details before remediation; it is now sanitized while remaining public. WebSocket origin validation, auth deadline, client cap, frame cap, stale socket close, and pre-auth broadcast suppression were added.

## 12. Tests and live validation

Pre-Phase-17 baseline: `75 passed, 1 warning`; 17 PowerShell scripts parsed cleanly; Python compile clean; `diagnose` reported `pass=15 warn=0 fail=0 critical=false`. Final post-change validation is summarized in section 14.3.

Live broker-native symbols were discovered exactly as `EURUSD`, `GBPUSD`, `USDJPY`, `XAUUSD`; no suffix rewriting was used for requests.

| Symbol | Bid | Ask | Price spread | Live result |
|---|---:|---:|---:|---|
| EURUSD | 1.09351 | 1.09354 | 0.00003 | fresh |
| GBPUSD | 1.30946 | 1.3094999999999999 | ~0.00004 | fresh |
| USDJPY | 159.554 | 159.562 | 0.008 | fresh |
| XAUUSD | 3652.84 | 3655.46 | 2.62 | `is_fresh=false`, age ~3923s |

Quote `point` and `digits` were absent/null because the current quote schema intentionally does not expose them; this is a contract observation, not fabricated data. A few quotes had timestamps 4–9s ahead of host UTC; with a fixed UTC+2 broker clock this is consistent with broker/host clock skew and is recorded as a risk rather than a code defect. XAUUSD had bid/ask but a stale timestamp; `is_fresh=false` correctly reflected that.

Candles M1, M5, M15, H1, H4, and D1 were fetched with broker-native symbols. Every series was ascending, had no duplicate open times, no invalid OHLC, no negative volume/spread, and no completed candle with a future end. Forming candles appeared on M15/H1/H4/D1. The D1 boundary at `22:00:00Z` is consistent with UTC+2 broker midnight.

EURUSD tick history returned 25 records in ascending order; 13 duplicate timestamps were observed before the identity-key fix. The normalized tick key was changed to include `last`, `volume`, and `flags` so genuine distinct ticks sharing a timestamp are no longer collapsed; exact broker duplicates remain reported as-is rather than fabricated away.

Live WebSocket validation confirmed auth ordering, invalid-input handling, quote/candle streams, unsubscribe, reconnect, multiple clients, and a slow client not blocking a healthy one. Resource snapshot during the audit: MT5 terminal ~324 MB working set, Caddy ~30 MB, API Python process ~8 MB.

## 13. Missing functionality

There are no trading/order endpoints and no market-data persistence, both intentional. `upsert_symbol` is dead code. Most endpoints do not declare a strict `response_model`. `DISABLE_BACKGROUND` was undocumented in `.env.example` before this phase and is now documented. Before remediation there was no WebSocket client cap, auth deadline, or origin enforcement; those controls now exist. Still absent: cache hit/miss metrics, `service_events` pruning, a DB migration mechanism beyond `create_all`, a dependency lockfile, and a reboot test for the four autostart tasks.

## 14. Confirmed defects, risks, and priorities

All findings below were cross-checked against source and, where applicable, live or mocked execution. Confirmed defects are kept separate from potential risks. No `CRITICAL` defect was confirmed; the highest confirmed items are `HIGH` because they could corrupt broker-time interpretation, duplicate completed-candle events, or weaken security boundaries. No endpoint paths were removed, no credential was exposed, and no trading/order operation was added.

### 14.1 Confirmed defects and fix status

| Severity | Confirmed defect | Source evidence | Fix and regression coverage | Status |
|---|---|---|---|---|
| HIGH | Broker-clock auto-detection rejected negative UTC offsets, so brokers behind UTC could be mapped to a wrong offset. DST fall-back refinements downward were also blocked. | `app/mt5/clock.py:103-149` | Offsets are now accepted in either direction within the skew bound, refinements work in both directions, and unsampled clocks expose `source`/`warning` via `describe()` (`app/mt5/clock.py:62-90`). Tests in `tests/test_clock.py`. | Fixed |
| HIGH | Historical ticks/candles could seed broker-clock auto-detection, making old records corrupt the offset estimate. | Historical sampling was removed; only a fresh `symbol_info_tick` is observed at `app/mt5/ticks.py:63-64` and `app/mt5/candles.py:85-89`; freshness bounds are enforced at `app/mt5/clock.py:121-128`. | `tests/test_clock.py` and `tests/test_hardening.py` assert out-of-window historical samples are ignored. | Fixed |
| HIGH | `to_utc_ms()` truncated broker milliseconds to whole seconds. | `app/mt5/clock.py:163-173` | Millisecond precision is preserved. `tests/test_clock.py` and `tests/test_phase17_clock_ticks.py` cover `.123` precision. | Fixed |
| HIGH | `MN1` candle completion used a fixed timeframe-seconds comparison rather than calendar-month rollover. | `app/mt5/clock.py:194-209` | Calendar month completion implemented. `tests/test_clock.py` covers month rollover. | Fixed |
| HIGH | MT5 initialization had no re-entry guard and could initialize twice. | `app/mt5/connection.py:132-174` | `initialize()` returns immediately when already initialized and clears stale state before a fresh attempt. Mocked tests in `tests/test_phase17_mt5.py`. | Fixed |
| HIGH | `(0, "OK")` and other stale MT5 error tuples were reported as real errors; `_last_error` was not cleared before operations. | `app/mt5/connection.py:72-82`, `247-274` | Success/zero/OK/None tuples are ignored and `_last_error` is cleared before initialize and each call. Mocked tests in `tests/test_phase17_mt5.py`. | Fixed |
| HIGH | Completion-candle events could repeat across wall-clock buckets even when the broker candle epoch was unchanged. | `app/services/market_collector.py:50-55`, `153-187` | Wall-bucket throttling is retained for cost, but emitted epochs are deduped per `(symbol,timeframe)` and cleared on unsubscribe. `tests/test_phase17_mt5.py` advances the wall bucket with the same epoch and asserts no duplicate. | Fixed |
| HIGH | Public `/api/v1/status` disclosed internal manager details: terminal path, broker, server, account login, last error, and full subscription snapshots. | `app/api/health.py:42-122` | Endpoint remains public but exposes only `initialized`, `terminal_connected`, `last_success`, `reconnect_attempts`, websocket client count, and an empty sanitized subscription list. `tests/test_phase17_security.py`. | Fixed |
| HIGH | WebSocket pre-auth clients were registered, so pre-auth `connection` broadcasts could be queued and delivered after login; there was no auth deadline, origin check, frame cap, or client cap. | `app/api/websocket.py:38-53`, `137-164`, `245-264`, `280-290`; `app/services/subscription_manager.py:22-61`, `164-166`; `app/config.py:99-102` | `Client.authenticated` gates fan-out; origin is checked against `CORS_ORIGINS`; first-message auth times out with 1008; capacity closes with 1013; frames cap at 1009; stale sockets are closed. Defaults are documented in `.env.example`. 18 tests in `tests/test_phase17_security.py`. | Fixed |
| MEDIUM | `/health`, `/ready`, and `/status` called blocking `manager.health()` on the event loop. | `app/api/health.py:52-122` | Calls now use `asyncio.to_thread`. Covered in `tests/test_phase17_security.py`. | Fixed |
| MEDIUM | Synchronous SQLite writes in the collector could block the event loop. | `app/services/market_collector.py:145-146`, `228-229` | `record_collector_stat()` and `record_event()` now run in worker threads. Mocked assertions in `tests/test_phase17_mt5.py`. | Fixed |
| MEDIUM | Distinct ticks sharing a timestamp were collapsed because dedupe used only time/bid/ask. | `app/services/data_validator.py:64-96` | Dedupe key now includes `last`, `volume`, and `flags`. `tests/test_phase17_clock_ticks.py` and `tests/test_hardening.py`. | Fixed |
| MEDIUM | Market-session windows stayed open all evening because the end hour was not excluded. | `app/api/market.py:23-27` | Same-day windows now use `start <= hour < end`; overnight logic is preserved. `tests/test_phase17_mt5.py` covers 10:00 UTC. | Fixed |
| MEDIUM | Broker suffixes such as `EURUSDm` were classified as `other` because uppercasing happened before suffix stripping. | `app/mt5/symbols.py:24-39` | Suffix stripping is case-insensitive for classification only; broker-native lookup is unchanged. `tests/test_phase17_mt5.py`. | Fixed |
| MEDIUM | A missing MetaTrader5 package could abort API startup. | `app/main.py:103-123` | `initialize_async()` exceptions are caught, sanitized by type only, recorded as startup errors, and the API continues. `tests/test_phase17_security.py`. | Fixed |
| MEDIUM | Tasklist fallback could mistake MT4 `terminal.exe` for MT5. | `app/mt5/terminal.py:140-169` | Fallback now accepts only `terminal64.exe`; explicit configured paths are still honored. Mocked subprocess test in `tests/test_phase17_mt5.py`. | Fixed |
| MEDIUM | `recent_events(request_id=...)` filtered only the newest rows after a limited fetch, so older matches were missed. | `app/database/repositories.py:38-58` | Filter is applied in SQL with a SQLAlchemy 2.1 compatibility shim and no Python post-filter. `tests/test_phase17_mt5.py` covers more than one fetch window. | Fixed |
| MEDIUM | Watchdog restart cooldown was not checked in the listener-present/unreachable branch, allowing a restart loop; stale health could also trigger an immediate API restart after terminal repair. | `scripts/watchdog.ps1:116-199` | `Test-RestartAllowed` is applied in both restart branches, `ApiRestarted`/`Action` are set only after success, `RestartSkipped` marks cooldown blocks, and health is re-read after terminal start. `tests/test_windows_scripts.py`; final live drill passed. | Fixed |
| MEDIUM | `bootstrap.ps1` passed literal quotes around `-PythonPath` and `-Domain` into `install.ps1`. | `scripts/bootstrap.ps1:64-76` | Plain values are splatted. `tests/test_windows_scripts.py`. | Fixed |
| MEDIUM | `install.ps1` always exited 0 even after failures. | `scripts/install.ps1:56`, `75-318`, `328-336` | `$installFailed` tracks Python, venv, pip, `.env`, autostart, and critical diagnostic failures; final URLs still print, then exit 1 on failure. Warnings remain non-fatal. `tests/test_windows_scripts.py`. | Fixed |
| MEDIUM | `LOCAL_DOMAIN`/`-Domain` was honored by hosts setup but not by Caddy or CORS guidance. | `scripts/install.ps1:195-240`; `caddy/Caddyfile` | Installer regenerates the loopback Caddyfile from the resolved domain before validation/startup, keeps the checked-in default matching, and warns that browser clients also need `CORS_ORIGINS`. README documents the override. `tests/test_windows_scripts.py`. | Fixed |
| MEDIUM | `stop_api.ps1` could target unrelated uvicorn instances and inaccurately claimed graceful shutdown. | `scripts/_common.ps1:501-514`; `scripts/stop_api.ps1:1-10`, `29-32` | `Get-ApiProcesses -Port` matches only `app.main:app` on the configured port, both start/stop helpers use it, and wording now says "terminates". Port-owner protection for non-Python services remains. `tests/test_windows_scripts.py`. | Fixed |
| MEDIUM | `reconnect_async()` slept after the final failed attempt, adding avoidable shutdown/loop delay. | `app/mt5/connection.py:219-240` | The final attempt returns without sleeping. Mocked test in `tests/test_phase17_mt5.py`. | Fixed |
| LOW | History-limiter stale/empty client cleanup was unreachable because the current timestamp was appended first. | `app/services/history_limiter.py:35-47` | Other clients are pruned before appending. `tests/test_phase17_mt5.py`. | Fixed |
| LOW | WebSocket error envelopes put `code`/`message` only inside `data`, while shipped examples read top-level fields. | `app/api/websocket.py:166-178` | Additive top-level `error` and `message` fields were added without removing `data`. `tests/test_phase17_security.py`. | Fixed |
| LOW | Junk WebSocket frames refreshed client liveness, keeping unauthenticated sockets alive indefinitely. | `app/api/websocket.py:245-290` | Only recognized, non-empty actions update `last_seen`. `tests/test_phase17_security.py`. | Fixed |

### 14.2 Potential risks and improvements (not confirmed defects)

| Severity | Risk / gap | Evidence | Priority / recommendation |
|---|---|---|---|
| MEDIUM | Broker timestamps for several liquid symbols were 4-9 seconds ahead of host UTC even with explicit UTC+2 configuration. | Live quote timestamps vs host UTC; broker clock `source=explicit`, `auto_detected=false`. | P1: monitor broker/host clock drift and NTP. No code defect was proven because the configured offset was applied consistently. |
| MEDIUM | XAUUSD returned bid/ask but a quote timestamp about 65 minutes old while `market_available=true`; `is_fresh=false` was correct. | Live quote table in section 12. | P1: confirm broker feed status for the demo symbol; keep relying on `is_fresh` and consumer-side stale handling. |
| MEDIUM | Four autostart tasks were registered and individually checked, but a full reboot test was not performed. | Section 10; task registration and status checks. | P2: schedule a maintenance-window reboot and verify all four tasks and domain resolution. |
| MEDIUM | Query-string `api_key` remains accepted for compatibility and may be retained by proxy/access logs. | Section 11. | P2: prefer the configured header in clients; consider deprecating query auth in a future contract revision. |
| LOW | Recent logs contain reconnect-failure errors produced during deliberate stop/start and watchdog drills; current status shows `reconnect_attempts=0`. | `check_status.ps1` recent errors vs live `/ready`. | P3: no action now; suppress or annotate drill-induced events if log noise matters. |
| LOW | Broker tick history contains duplicate timestamps. | 13 duplicate timestamps in 25 live records. | P3: broker data-quality observation. Identity-based dedupe now preserves genuinely distinct ticks; exact broker duplicates are not fabricated away. |
| LOW | `service_events` has no retention pruning; `collector_stats` does. | Section 9. | P3: add configurable retention if long-running growth becomes measurable. |
| LOW | No dependency lockfile and loose version bounds. | Section 5. | P3: add a lockfile/constraints file for reproducible installs. |
| LOW | No DB migration mechanism beyond `create_all`. | Section 9. | P3: add migrations before introducing schema changes. |
| IMPROVEMENT | Quote `point` and `digits` are absent/null because the current schema does not expose them. | `app/schemas/market.py:26-42`; live quote check. | P3: add them additively if consumers need broker contract size metadata. |
| IMPROVEMENT | Most endpoints do not declare strict `response_model`s. | Section 13. | P3: strengthen response contracts incrementally without breaking current clients. |
| IMPROVEMENT | `pandas` is unused by `app/`; `upsert_symbol` is dead code. | Sections 5 and 13. | P4: remove or justify during a future cleanup pass. |
| IMPROVEMENT | Resource footprint is modest but nonzero: MT5 terminal ~324 MB, Caddy ~30 MB, API Python ~8 MB. | Live process snapshot. | P4: monitor if the host is memory constrained. |

### 14.3 Final validation record

- Full test suite after all fixes: `121 passed, 1 warning` (the warning is the existing Starlette/httpx `testclient` deprecation notice).
- Targeted suites: clock/tick regressions `2 passed`; MT5/collector/script regressions `46 passed`; API security suite `43 passed`; Windows script suite `29 passed`.
- `python -m compileall -q app scripts tests`: clean.
- PowerShell parser check across all 18 `.ps1` files: `parse_errors=0`.
- `git diff --check`: no whitespace errors; only Git CRLF-normalization warnings.
- `diagnose.ps1 -Json`: `pass=15 warn=0 fail=0 critical=false`.
- `check_status.ps1 -Json`: health reachable, HTTP 200, `status=ok`, `terminal_connected=true`, `data_freshness=live`, `degraded=false`.
- Direct and domain health after API restart: both `ok`, terminal connected, freshness live; domain `/docs` HTTP 200.
- Live WebSocket smoke after restart: auth ordering, query auth, ping/pong, invalid JSON/action errors, quote/candle streams, unsubscribe, multiple clients, slow-consumer isolation, and reconnect all passed.
- Live HTTP security probes: unauthenticated and wrong-key accesses to protected endpoints returned 401 with generic envelopes and `WWW-Authenticate: ApiKey`; allowed CORS origin received the allow-origin header and a disallowed preflight did not.
- Final watchdog failover drill after script changes: `Action=api-started`, `ApiRestarted=true`, `RestartSkipped=false`, `Error=null`, clean JSON, and health restored to `ok`/live.
- Baseline CI for `83a1db8`: GitHub Actions run `46065541388` completed with `conclusion=success`. The post-change CI result is checked after the final push.
