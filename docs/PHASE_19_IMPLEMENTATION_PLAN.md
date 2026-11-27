# Phase 19 Implementation Plan — Multi-Timeframe Snapshot Engine

**Repository:** `nazirumar/mt5api`
**Baseline:** current working tree (Phase 18 uncommitted on top of `e2f23f0`)
**Audit date:** 2026-11-27 UTC
**Baseline test result:** `143 passed, 1 warning` (Starlette/httpx `TestClient` deprecation notice only)

## 1. Audit scope and method

Three read-only audit streams inspected `app/`, `tests/`, `docs/`, `examples/`, `.env.example`, `requirements.txt`, `README.md`, and `.github/workflows/ci.yml`, plus targeted source searches for snapshot, alignment, synchronization, cache, and history-coordination code. No files were changed during the audit.

## 2. Architecture findings

### 2.1 API, auth, and envelope

- REST routers are aggregated under `/api/v1` by `app/api/router.py:7-16`. The `market` router is registered at `:13`; a new `snapshot` router can be added additively at the same level.
- Every market-data router already declares router-level `Depends(require_api_key)` (e.g. `app/api/market.py:18`). REST auth reads only the configured header (`X-API-Key` by default) via `app/core/security.py:13-22`; query-string `api_key` is WebSocket-only (`app/api/websocket.py:78-83`).
- Success envelope helper `ok()` (`app/api/__init__.py:10-15`) returns `{"success": True, "data": ..., "meta": {"request_id": ...}}`. `APIResponse[T]` is defined at `app/schemas/common.py:12-15`.
- Request IDs are generated/echoed by middleware (`app/main.py:161-203`) and available via `get_request_id()` (`app/core/context.py:10-12`).
- Domain errors map to existing codes/statuses at `app/core/exceptions.py:6-60` and `app/main.py:205-250`: `SYMBOL_NOT_FOUND` 404, `INVALID_TIMEFRAME`/`INVALID_REQUEST` 400, `VALIDATION_ERROR` 422, `RATE_LIMITED` 429, `MT5_*` 502/503.
- `HistoryLimiter` (`app/services/history_limiter.py:15-62`) is the only history guard: 30 calls/min per client and global concurrency 2 by default. Candles (`app/api/candles.py:32-35`), ticks (`app/api/ticks.py:28-31`), and Phase 18 context (`app/api/market.py:57-59`) acquire one slot for the whole request.

### 2.2 MT5 and data services

- `MT5ConnectionManager.call()` (`app/mt5/connection.py:247-277`) is the single serialized MT5 boundary, holding an `RLock` (`app/mt5/connection.py:41`) for every data call. Async callers use `asyncio.to_thread`. No second connector is needed or permitted.
- `CandleService.get_candles()` (`app/mt5/candles.py:51-119`) accepts an optional pre-fetched `info` (added in Phase 18), normalizes the timeframe, bounds the count to `MAX_CANDLE_LIMIT`, and returns UTC-normalized candles via `BrokerClock.convert_candles()`.
- `QuoteService.get_quote()` (`app/mt5/quotes.py:68-77`) also accepts optional `info`. `QuoteCache` (`app/services/cache.py:43-101`) holds the latest quote per symbol; `refresh_freshness()` recomputes age from a fixed reference time.
- `CandleCache` (`app/services/candle_cache.py:11-45`) stores only the latest completed candle per `(symbol, timeframe)`; it is not a series cache and must not be treated as one.
- `BrokerClock.convert_candles()` (`app/mt5/clock.py:211-228`) is authoritative for UTC timestamps and `completed` flags (`_is_completed()` at `:194-209`, MN1 special case included).
- `TIMEFRAME_SECONDS` and `normalize_timeframe()` (`app/mt5/timeframes.py:8-30,57-63`) already support M1..MN1, including the requested M1/M5/M15/M30/H1/H4/D1.
- `MarketCollector` (`app/services/market_collector.py:117-224`) polls quotes sequentially and completed candles in one coroutine; it does not take a history-limiter slot and only contends at the manager RLock.
- Multiple history calls are therefore serialized at three layers: the manager RLock, the `HistoryLimiter` semaphore, and (in Phase 18) per-key single-flight locks. Uncontrolled parallel history calls would add no throughput and would increase lock contention, so Phase 19 stays sequential inside one worker thread.

### 2.3 Phase 18 reuse

- `MarketContextEngine` (`app/context/engine.py:27-333`) already provides: exact broker-symbol resolution with TTL cache and single-flight (`_symbol_info`, `:63-81`), quote-cache reuse (`_quote`, `:101-113`), normalized quote context (`_quote_context`, `:115-144`), per-timeframe candle series with TTL cache and single-flight (`_series`, `:146-160`), per-timeframe context (`_timeframe_context`, `:183-217`), data quality (`analytics.assess_data_quality`), session context (`app/context/sessions.py:72-138`), and UNKNOWN suppression (`analytics.unknown_classifications`, `:900-909`).
- `build_context()` (`app/context/engine.py:255-333`) captures one UTC reference time for the whole request, takes a list of timeframes, and returns `timeframes` keyed by normalized timeframe. It is already the closest thing to a coordinated snapshot.
- Phase 18 does not expose these data helpers publicly, and its candle return is capped at `CONTEXT_MAX_RETURN_CANDLES` (25) when `include_candles=True`. Phase 19 must not duplicate these calculations or re-implement data retrieval.

### 2.4 Tests, docs, and configuration

- `pytest.ini` uses `asyncio_mode = strict`. Tests use the shared `FakeMT5`/`settings`/`app` fixtures from `tests/conftest.py`, `HEADERS = {"X-API-Key": "test-api-key"}`, and `with TestClient(app) as client:`.
- `FakeMT5.copy_rates_from_pos()` returns at most 20 synthetic ascending candles; per-timeframe failure/order/forming-candle scenarios require monkeypatching `CandleService.get_candles`.
- CI (`.github/workflows/ci.yml`) installs `requirements.txt` and runs `python -m pytest -q` on Windows with Python 3.12/3.13. No new dependency is needed for Phase 19.
- No snapshot, alignment, or synchronization engine exists. No golden/approval-test infrastructure exists.
- `README.md`, `.env.example`, `examples/python_client.py`, and `examples/nextjs_client.ts` already document Phase 18 and provide extension points for a new endpoint.

## 3. Reuse decisions

| Existing component | Phase 19 decision |
|---|---|
| `MT5ConnectionManager.call()` | Keep as the only MT5 boundary; no new connector, no parallel MT5 fan-out. |
| `SymbolService` / Phase 18 symbol cache | Reuse exact broker-native resolution through a thin public Phase 18 method. |
| `QuoteService` / `QuoteCache` / `refresh_freshness()` | Reuse through Phase 18 quote helper; no second quote poller. |
| `CandleService.get_candles(info=...)` | Reuse through Phase 18 series helper; one sequential call per requested timeframe. |
| `TIMEFRAME_SECONDS` / `normalize_timeframe()` | Use for validation, role mapping, duration metadata, and higher/lower ordering. |
| Phase 18 analytics (`trend`, `regime`, `momentum`, `volatility`, `price_position`, `candle_structure`, `spread`, `assess_data_quality`, `unknown_classifications`) | Reuse unchanged through thin public Phase 18 wrappers. **Do not reimplement.** |
| `app/context/sessions.py::session_context` | Reuse for per-timeframe and top-level session context. |
| `HistoryLimiter` | One slot for the entire snapshot request. |
| `ok()` / exception handlers / request-ID middleware | Use unchanged; new route adds no custom envelope. |
| `CandleCache` | Not used as a source; it holds only one latest candle, not a series. |

## 4. Proposed design

### 4.1 New package `app/snapshot/`

- `__init__.py` — exports `SnapshotEngine`.
- `engine.py` — synchronous orchestration; called via `asyncio.to_thread`. Builds symbol, quote, per-timeframe series, contexts, quality, synchronization, and alignment.
- `alignment.py` — pure deterministic cross-timeframe alignment and conflict detection.
- `synchronization.py` — pure retrieval-synchronization classification and warning generation.
- `quality.py` — pure snapshot-level completeness/quality aggregation.
- `roles.py` — informational timeframe role labels (D1 macro, H4 major structure, H1 trend, M15 setup, M5 execution, M1 detailed execution); no decision forcing.

### 4.2 Additive public wrappers on Phase 18 engine

To avoid duplicating Phase 18 logic, add thin public methods to `MarketContextEngine` that delegate to existing private helpers without changing their behavior or the `/market/context` response:

- `resolve_symbol(symbol) -> dict`
- `quote_snapshot(symbol, info=None, now_epoch=None) -> dict`
- `series(symbol, timeframe, limit, info) -> list[dict]`
- `context_for_series(series, quote, include_indicators=True, include_structure=True, include_candles=False) -> dict`
- `quality_for_series(info, quote, series, connected) -> dict`
- `connected() -> bool`
- `broker_time() -> str | None`

### 4.3 New endpoint

`GET /api/v1/market/snapshot/{symbol}` on a new `app/api/snapshot.py` router (prefix `/market/snapshot`, router-level `require_api_key`), included in `app/api/router.py`.

Query parameters:

| Parameter | Default | Bound / behavior |
|---|---:|---|
| `timeframes` | `SNAPSHOT_DEFAULT_TIMEFRAMES` | Comma list; deduplicated, normalized, max `SNAPSHOT_MAX_TIMEFRAMES`; empty/invalid/duplicate-with-different-spelling rejected |
| `limit` | `SNAPSHOT_DEFAULT_LIMIT` | 1..`SNAPSHOT_MAX_LIMIT` (also bounded by `MAX_CANDLE_LIMIT`) |
| `include_context` | `true` | Include Phase 18 context blocks |
| `include_candles` | `true` | Include candles, capped at `SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME` |
| `include_indicators` | `true` | Passed through to Phase 18 context |
| `include_structure` | `true` | Passed through to Phase 18 context |

The route acquires one `HistoryLimiter` slot, then calls `SnapshotEngine.build_snapshot()` via `asyncio.to_thread`, and returns `ok(payload)` with `response_model=APIResponse[SnapshotResponse]`.

### 4.4 Data flow

1. Capture `snapshot_id`, UTC generation time, wall-clock start, and monotonic start.
2. Validate and normalize timeframes and limit → deterministic `InvalidRequestError`/`InvalidTimeframeError` with details.
3. Resolve broker-native symbol once (Phase 18 cache).
4. Fetch/normalize one quote snapshot once (Phase 18 cache + live fallback); record quote retrieval time and freshness.
5. For each requested timeframe, **sequentially**:
   1. Fetch candles through the Phase 18 series helper (TTL cache + single-flight).
   2. Normalize chronologically: sort by `epoch`, drop duplicate epochs, keep `completed` flags untouched.
   3. Build metadata: duration, requested/returned/available counts, latest candle timestamp and completion (`COMPLETED`/`FORMING`/`UNKNOWN`), availability, candle freshness, retrieval timestamp and duration, role label.
   4. Build Phase 18 context and per-timeframe data quality.
   5. Capture any exception as a structured per-timeframe error and continue with the remaining timeframes.
6. Compute top-level session context once; attach to each timeframe context.
7. Apply UNKNOWN suppression to any context when the overall snapshot is unusable.
8. Compute synchronization, alignment, and overall snapshot quality from successful timeframes only.
9. Set `snapshot_status` to `COMPLETE`, `PARTIAL`, or `UNAVAILABLE` and return.

### 4.5 Alignment methodology (deterministic, non-predictive)

- Input: trend classification from each successfully retrieved timeframe with sufficient data; missing/unknown timeframes are counted and reported, never guessed.
- Counts: bullish, bearish, neutral, mixed, unknown.
- `alignment_ratio = max(bullish, bearish, neutral, mixed) / analyzed_count`, where `analyzed_count` excludes unknown. It measures agreement among descriptive classifications only; it is not a probability or expected success rate.
- Timeframes are ordered by descending duration for conflict detection (higher timeframe first). A conflict is an opposite directional pair (BULLISH vs BEARISH) between consecutive available timeframes.
- Classification order:
  1. `INSUFFICIENT_DATA` when fewer than `SNAPSHOT_MIN_ALIGNED_TIMEFRAMES` timeframes are analyzable.
  2. `UNKNOWN` when no directional classification is available.
  3. `CONFLICTING` when conflicts exist and `alignment_ratio < SNAPSHOT_ALIGNMENT_DOMINANT_RATIO`.
  4. `FULLY_ALIGNED` when all analyzable classifications are the same directional class.
  5. `MOSTLY_ALIGNED` when a directional class reaches `SNAPSHOT_ALIGNMENT_DOMINANT_RATIO`.
  6. `MIXED` otherwise.
- Higher timeframes are not assumed correct. No BUY/SELL label, probability, or score implying predictive accuracy is emitted.

### 4.6 Synchronization methodology

Three distinct concepts are reported separately:

1. **Candle time alignment** — latest completed candle epochs per timeframe; explicit note that different timeframes close at naturally different times.
2. **Retrieval synchronization** — spread between per-timeframe retrieval timestamps, compared with configurable tight/acceptable tolerances.
3. **Quote freshness** — from the Phase 18 quote context (`FRESH`/`STALE`/`VERY_STALE`/`UNAVAILABLE`).

Classification: `SYNCHRONIZED` (all present, tight tolerance, fresh quote), `ACCEPTABLE` (within acceptable tolerance), `PARTIALLY_SYNCHRONIZED` (missing/failed timeframe but remaining data within tolerance), `UNSYNCHRONIZED` (outside tolerance or unusable quote), `UNKNOWN` (no retrieval timestamps). Warnings are explicit; the response never claims simultaneity.

### 4.7 Snapshot quality and status

- Per-timeframe quality reuses `analytics.assess_data_quality` through Phase 18.
- Overall quality = mean successful timeframe score scaled by `successful / requested`, plus penalties/warnings for unusable quote, disconnection, and missing timeframes. Grade thresholds reuse Phase 18 `CONTEXT_DATA_QUALITY_*` settings.
- `missing_timeframes`, `timeframe_errors`, and `warnings` are always present. A single failed timeframe does not fail the whole request; no timeframes available makes the snapshot `UNAVAILABLE`.

## 5. New files

- `app/snapshot/__init__.py`, `app/snapshot/engine.py`, `app/snapshot/alignment.py`, `app/snapshot/synchronization.py`, `app/snapshot/quality.py`, `app/snapshot/roles.py`
- `app/api/snapshot.py`
- `app/schemas/market_snapshot.py`
- `tests/test_phase19_alignment.py`, `tests/test_phase19_synchronization.py`, `tests/test_phase19_api.py`
- `docs/PHASE_19_IMPLEMENTATION_PLAN.md`, `docs/PHASE_19_MULTI_TIMEFRAME_SNAPSHOT.md`

## 6. Modified files

- `app/context/engine.py` — additive public wrappers only.
- `app/api/router.py` — include the snapshot router.
- `app/dependencies.py` — add `snapshot_engine` to `AppContext`.
- `app/main.py` — construct `SnapshotEngine` and pass it to `AppContext`.
- `app/config.py` — additive `SNAPSHOT_*` settings and validation.
- `.env.example`, `README.md`, `examples/python_client.py`, `examples/nextjs_client.ts` — document/expose the endpoint.

## 7. Test plan

Pure tests:
- alignment: all bullish, all bearish, mixed, conflicting, insufficient, unknown, missing timeframes, adjacent conflict detection, no-signal disclaimer.
- synchronization: all classifications, tolerance boundaries, missing timeframe, stale quote, natural candle offsets, duration metadata.

API/mocked-MT5 tests:
- auth 401, request-ID/envelope, broker-native symbol suffixes, default/custom timeframes, dedupe/validation errors, max limit.
- all timeframes available; one missing timeframe (monkeypatched failure); two conflicting; all bullish; all bearish; mixed; stale quote; disconnected; insufficient history; invalid symbol; invalid timeframe; partial retrieval failure; empty candles; forming candle; out-of-order candles.
- candle order/dedupe, completion flags, UTC timestamp format, rate-limit 429, response schema keys, bounded candle counts.

Regression: run the complete suite and confirm `143` pre-existing tests still pass. No live MT5 claim will be made.

## 8. Performance considerations

- One worker thread per request; **no** thread/process per timeframe and no parallel MT5 history fan-out.
- Reuse Phase 18 TTL caches and single-flight locks; do not build a second cache system.
- One history-limiter slot per snapshot.
- Candle count bounded by `SNAPSHOT_MAX_LIMIT` and `MAX_CANDLE_LIMIT`; returned candles additionally capped per timeframe.
- No new dependency, no model inference, no writes, no trade execution.
- Retrieval timestamps and cached-hit notes make cache reuse visible instead of hidden.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Phase 18 wrappers could change existing behavior | Wrappers only delegate to existing private methods; `/market/context` tests must remain green. |
| Per-timeframe failure could abort the whole snapshot | Catch and record per-timeframe errors; continue with remaining timeframes. |
| Quote and candles are not atomic | Explicit synchronization classification and warnings; no simultaneity claim. |
| Forming candles could be treated as complete | Preserve `completed`; analytics already filters `completed is True`; metadata reports `FORMING`. |
| Out-of-order or duplicate candles | Sort by epoch and dedupe before analysis; add warnings. |
| Alignment could imply predictive accuracy | Document that counts/ratios are descriptive; no probability or signal fields. |
| Response size growth | Defaults and hard caps on timeframes, limit, and returned candles. |
