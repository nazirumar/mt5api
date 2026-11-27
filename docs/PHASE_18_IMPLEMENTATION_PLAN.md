# Phase 18 Implementation Plan — AI Market Context Engine

**Repository:** `nazirumar/mt5api`  
**Baseline commit:** `e2f23f0`  
**Audit date:** 2026-11-27 UTC  
**Baseline test result:** `121 passed, 1 warning` (Starlette/httpx `testclient` deprecation notice only)

## 1. Audit scope and method

Read-only review of `app/`, `tests/`, `docs/`, `examples/`, `scripts/`, `.env.example`, `requirements.txt`, `README.md`, and `.github/workflows/ci.yml`, plus direct source searches for any existing context/trend/regime/momentum/volatility logic. Three independent review streams covered API/security conventions, MT5/data services, and tests/docs/examples. No files were changed during the audit.

## 2. Current architecture findings

### 2.1 Application and API

- `app/main.py:58-60` builds the app from `Settings`; `app/main.py:139-147` creates the FastAPI instance with docs enabled; `app/main.py:288` exposes the module-level app.
- `app/api/router.py:7-19` aggregates REST routers under `/api/v1`. The `market` router is already registered at `app/api/router.py:13` and is protected by `dependencies=[Depends(require_api_key)]` at `app/api/market.py:13`.
- `app/dependencies.py:15-32` defines `AppContext`; `app/main.py:83-101` constructs and stores it on `app.state.context`.
- REST authentication is header-only (`app/core/security.py:20-22`, `app/dependencies.py:39-47`) using `X-API-Key` by default (`app/config.py:60`), with current/previous key rotation (`app/config.py:145-151`). Query-string `api_key` is WebSocket-only (`app/api/websocket.py:78-83`).
- There is no global rate limiter. The only limiter is `HistoryLimiter` (`app/services/history_limiter.py:15-62`), applied to candle and tick history routes (`app/api/candles.py:33-35`, `app/api/ticks.py:29-31`) with per-client 30/min default and concurrency 2.
- Request IDs are set by middleware (`app/main.py:156-184`) and injected into `ok()` (`app/api/__init__.py:10-15`) and all error handlers (`app/main.py:200-245`).
- Success envelope: `{"success": true, "data": ..., "meta": {"request_id": ...}}` (`app/api/__init__.py:10-15`). Errors use `{"success": false, "error": {"code", "message", "details"}, "meta": {...}}` with domain status codes from `app/core/exceptions.py:6-60`.
- Query parameters are declared with `Annotated[..., Query(...)]` (`app/api/candles.py:24-31`, `app/api/symbols.py:24-28`).

### 2.2 MT5 and data services

- `MT5ConnectionManager.call()` (`app/mt5/connection.py:247-277`) is the single serialized MT5 boundary, using an `RLock` (`app/mt5/connection.py:41`). Async callers use `asyncio.to_thread`.
- `QuoteService.quote_from_tick()` (`app/mt5/quotes.py:32-66`) derives bid/ask/mid-adjacent spread, spread points/pips, age, freshness, and availability. It never fabricates missing ticks. `get_quote()` (`app/mt5/quotes.py:68-75`) currently fetches `symbol_info` and `symbol_info_tick` separately.
- `SymbolService.symbol_info()` (`app/mt5/symbols.py:71-90`) already exposes `digits`, `point`, `tick_size`, `tick_value`, contract size, and trade mode. `get_symbol()` (`app/mt5/symbols.py:110-118`) performs exact broker-native lookup.
- `CandleService.get_candles()` (`app/mt5/candles.py:51-117`) supports all MT5 timeframes via `app/mt5/timeframes.py:8-70`, validates ranges, and returns normalized candles with UTC timestamps and `completed` flags.
- `BrokerClock` (`app/mt5/clock.py:35-91`, `151-228`) handles IANA/fixed-offset broker time and completion semantics. Completion/freshness currently use implicit host `time.time()`.
- `QuoteCache` (`app/services/cache.py:43-101`) stores only the latest quote per symbol; `CandleCache` (`app/services/candle_cache.py:11-45`) stores only the latest completed candle per pair. No rolling spread history exists.
- `MarketCollector` (`app/services/market_collector.py:117-203`) polls quotes and subscribed completed candles in one shared loop, with per-symbol quote error state and epoch-based candle dedupe.
- `app/api/market.py:15-43` contains the only session engine: static approximate UTC windows with end-exclusive overnight logic. It is broker-independent and has no verified broker schedule or DST model.
- `app/services/data_validator.py:11-61` validates OHLC shape and normalizes/dedupes candles; it does not compute indicators.

### 2.3 Existing calculations

No endpoint, service, schema, test, or document computes trend, regime, momentum, volatility, ATR, candle structure, price position, or market context. The only derived values are quote spread/freshness, health freshness, broker-clock description, session open flags, and candle completion.

### 2.4 Tests, docs, examples, CI

- Tests use `tests/conftest.py` fake MT5 (`FakeMT5` at `:24-132`, `app` fixture at `:161-163`), `HEADERS = {"X-API-Key": "test-api-key"}`, and `TestClient(app)`.
- `pytest.ini:1-6` uses strict asyncio mode; async tests need `@pytest.mark.asyncio`.
- CI (`.github/workflows/ci.yml:31-38`) installs requirements and runs `python -m pytest -q` on Windows with Python 3.12 and 3.13.
- Docs live in `docs/PHASE_17_AUDIT_REPORT.md`; examples are `examples/python_client.py`, `examples/websocket_client.py`, and `examples/nextjs_client.ts`.

## 3. What can be reused

| Existing component | Reuse decision |
|---|---|
| `MT5ConnectionManager.call()` | Keep as the only MT5 access path; no new connection or collector. |
| `SymbolService.get_symbol()` | Reuse exact broker-native lookup and metadata; cache result briefly in the context engine. |
| `QuoteService.quote_from_tick()` | Reuse pure quote shaping. Add an optional `info` argument to `get_quote()` so the context path reuses already-fetched symbol info. |
| `CandleService.get_candles()` | Reuse for historical context candles through the existing `HistoryLimiter`. |
| `TIMEFRAME_SECONDS` / `normalize_timeframe()` | Reuse for timeframe validation and completion checks. |
| `BrokerClock` | Reuse for broker time display and UTC correctness. |
| `QuoteCache` / `CandleCache` | Read cached latest quote where safe; do not treat single latest candle as a series. |
| `HistoryLimiter` | Acquire one slot for the whole context request so a multi-timeframe response cannot multiply unbounded history reads. |
| `ok()` / exception handlers / request-ID middleware | Use unchanged; new route adds no custom envelope. |
| `app/api/market.py` session windows | Move the pure helpers into a shared context session module and import them back into the existing route, preserving `/market/sessions` behavior. |

## 4. Proposed implementation

### 4.1 New package

- `app/context/__init__.py`
- `app/context/analytics.py` — pure deterministic calculations and classifications. No MT5 calls, no I/O, no hidden clock reads.
- `app/context/sessions.py` — shared session windows and deterministic session context builder.
- `app/context/engine.py` — synchronous orchestration service with bounded TTL caches and per-key single-flight locks. Runs in a worker thread from the API.

### 4.2 New schemas

- `app/schemas/market_context.py` — Pydantic models for quote context, trend, regime, momentum, volatility, price position, spread, candle structure, session, data quality, per-timeframe context, and the top-level response.

### 4.3 Endpoint

Add `GET /api/v1/market/context/{symbol}` inside the existing protected `market` router. Query parameters:

- `timeframe` (default `M5`)
- `timeframes` (optional comma-separated list, capped by `CONTEXT_MAX_TIMEFRAMES`)
- `lookback` (default `CONTEXT_DEFAULT_LOOKBACK`, bounded to `CONTEXT_MAX_LOOKBACK`)
- `include_candles` (default `false`)
- `include_indicators` (default `true`)
- `include_structure` (default `true`)

The handler:

1. Uses the router-level API-key dependency.
2. Uses `ctx.history_limiter.acquire(request)` for the whole request.
3. Calls `ctx.context_engine.build_context(...)` via `asyncio.to_thread`.
4. Returns `ok(payload)` with `response_model=APIResponse[MarketContextResponse]`.
5. Raises existing `SymbolNotFoundError`, `InvalidTimeframeError`, `InvalidRequestError`, `MT5NotInitializedError`, and `RateLimitError` as appropriate.

No other endpoint paths or response shapes change. `/api/v1/market/sessions` stays intact.

### 4.4 Classification methods

All thresholds live in `Settings` and `.env.example`; no magic numbers are scattered through the code.

- **Quote freshness:** `FRESH` within the quote stale window; `STALE` within `CONTEXT_QUOTE_VERY_STALE_MULTIPLIER`; `VERY_STALE` beyond it; `UNAVAILABLE` when timestamp/bid/ask are missing.
- **Trend:** SMA fast/slow relationship, linear-regression slope, higher-high/higher-low structure, and close-to-close directional consistency. Score is -4..+4; `BULLISH`/`BEARISH` at ±2, conflicting signs yield `MIXED`, no directional evidence yields `NEUTRAL`, insufficient data yields `UNKNOWN`.
- **Regime:** ATR expansion/contraction ratio, ATR percent, body-to-range ratio, net movement in ATR units, and directional consistency. Rules produce `TRENDING`, `RANGING`, `VOLATILE`, `QUIET`, `TRANSITIONAL`, or `UNKNOWN`, with measurements and explanation.
- **Momentum:** close-to-close move over `CONTEXT_MOMENTUM_LOOKBACK`, normalized by ATR, plus acceleration versus the previous equal-length window. Thresholds yield `STRONG_*`, `*`, `NEUTRAL`, or `UNKNOWN`.
- **Volatility:** simple ATR over `CONTEXT_ATR_PERIOD`, ATR percent, current/average candle range, recent/prior ATR ratio, and a historical range percentile when enough samples exist. Classification uses configurable ATR-percent bands; expansion/contraction is reported separately.
- **Candle structure:** body, wicks, range, and ratios for completed candles. Pattern labels are emitted only for explicit ratio rules (`STRONG_*_CANDLE`, `LONG_*_WICK`, `DOJI_LIKE`, `SMALL_BODY`, `NORMAL`).
- **Price position:** recent high/low, midpoint, and `(close-low)/(high-low)*100`; zones use configurable lower/upper percentages and a neutral description.
- **Spread:** uses current quote spread plus integer candle-spread samples as normalized points history; compares current points with recent average points via configurable elevated/abnormal ratios. No cross-symbol absolute threshold is used.
- **Session:** reuses the static UTC windows; returns `classification=UNKNOWN` and `verified=false` because broker trading hours cannot be verified from current MT5 data. Approximate active sessions, overlap, broker time, and next transition are still returned.
- **Data quality:** weighted 0-100 score from symbol availability, quote presence/freshness, candle count, OHLC validity, duplicate/monotonic timestamps, spread availability, and MT5 connection. Grades and `usable` are configurable; if unusable, all context classifications are forced to `UNKNOWN`.

The score is explicitly a data-reliability score, not a prediction, win probability, or buy/sell confidence.

### 4.5 Performance design

- One history-limiter slot per request, regardless of timeframe count.
- Engine-level TTL cache for symbol info (default 300s) and candle series (default 30s), keyed by `(symbol, timeframe, lookback)`.
- Per-key `threading.Lock` single-flight prevents duplicate MT5 calls for concurrent identical requests.
- Quote uses `QuoteCache` when fresh; otherwise one direct quote call with reused symbol info.
- No catalog scans, no new collectors, no worker per symbol, no new dependencies, no persistence, and no heavyweight model.
- All MT5 work stays behind `MT5ConnectionManager.call()` and runs in `asyncio.to_thread`.

## 5. New files

- `docs/PHASE_18_IMPLEMENTATION_PLAN.md` (this file)
- `docs/PHASE_18_MARKET_CONTEXT_ENGINE.md`
- `app/context/__init__.py`
- `app/context/analytics.py`
- `app/context/sessions.py`
- `app/context/engine.py`
- `app/schemas/market_context.py`
- `tests/test_phase18_analytics.py`
- `tests/test_phase18_api.py`

## 6. Modified files

- `app/config.py` — new `CONTEXT_*` settings and validation.
- `app/dependencies.py` — add `context_engine` to `AppContext`.
- `app/main.py` — instantiate `MarketContextEngine`.
- `app/mt5/quotes.py` — optional `info` parameter on `get_quote()` (additive/backward compatible).
- `app/api/market.py` — import shared session helpers and add the context route.
- `app/schemas/market.py` — no breaking changes; context models live separately.
- `.env.example` — document all new settings.
- `README.md` — endpoint, config, methodology summary, examples, limitations.
- `examples/python_client.py` — add `market_context()` method.
- `examples/nextjs_client.ts` — add `fetchMarketContext()` and types.

## 7. Testing plan

1. Pure deterministic analytics tests with synthetic candle lists: trend (all five classes), regime (all six), momentum (all six), ATR/volatility, price position, candle structure, spread, and data quality/grade/unusable behavior.
2. Session tests with an injected UTC `now`, including overlap and overnight windows.
3. API tests using the shared fake MT5 fixture: auth 401, success envelope, request ID, invalid symbol, broker suffix lookup, invalid timeframe/lookback, stale quote, insufficient history, MT5 disconnection, multi-timeframe, and include flags.
4. Rate-limit test using the existing `HistoryLimiter` path.
5. Regression: full `pytest -q`, `compileall`, PowerShell parse, `git diff --check`, direct/domain health after restart, and WebSocket smoke.
6. Live MT5 validation is separate and will be reported only if actually executed; mocked tests do not prove broker data correctness.

## 8. Compatibility and security

- No existing endpoint, auth rule, rate-limit policy, config name, or startup automation is removed.
- New settings all have defaults, so existing test fixtures and `.env` files continue to work.
- No API secrets in responses or logs; request IDs remain automatic.
- No trading/order operations are introduced.
- Classifications are documented as descriptive context, not signals or guarantees.
