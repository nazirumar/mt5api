# Phase 19 — Multi-Timeframe Market Snapshot Engine

Phase 19 adds a single read-only endpoint that presents one broker-native symbol across several MT5 timeframes in one bounded response. It is an additive layer over Phase 18: the snapshot engine calls the existing `MarketContextEngine`, caches, broker clock, quote/candle services, serialized MT5 manager, authentication, request IDs, response envelope, and history limiter. It does **not** add a connector, a second analytics implementation, a model, or trading functionality.

All data in the response comes from real MT5 quotes and completed/forming history. Missing or failed data is reported explicitly; the server never fabricates a quote or candle.

> Classifications are descriptive measurements of past/current data. They are not BUY/SELL signals, win probabilities, predictions, or expected trade outcomes.

## 1. Endpoint

```text
GET /api/v1/market/snapshot/{symbol}
```

Authentication is required. REST clients must send the API key in the configured header (default `X-API-Key`). `?api_key=` is accepted only by the WebSocket endpoint and must not be used for REST.

```powershell
curl.exe -s -H "X-API-Key: $env:API_KEY" `
  "http://127.0.0.1:8080/api/v1/market/snapshot/XAUUSDm?timeframes=M15,H1,H4,D1&limit=100"
```

### Query parameters

| Parameter | Default | Validation | Purpose |
|---|---:|---|---|
| `timeframes` | `SNAPSHOT_DEFAULT_TIMEFRAMES` (`M1,M5,M15,H1,H4,D1`) | Comma list, normalized, deduplicated, at most `SNAPSHOT_MAX_TIMEFRAMES` | Timeframes to include. Empty entries, duplicates, unknown tokens, and over-long lists are rejected. |
| `limit` | `SNAPSHOT_DEFAULT_LIMIT` (`100`) | `1..SNAPSHOT_MAX_LIMIT` (`500`); FastAPI also caps at `5000` | Candles requested per timeframe from MT5 history. |
| `include_context` | `true` | boolean | Include Phase 18 trend/regime/momentum/volatility/price-position/spread/structure context. |
| `include_candles` | `true` | boolean | Return candles, capped per timeframe by `SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME`. |
| `include_indicators` | `true` | boolean | Passed through to Phase 18 indicator summary. |
| `include_structure` | `true` | boolean | Passed through to Phase 18 candle-structure analysis. |

Supported tokens are the MT5 tokens already supported by the service: `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, and `MN1`. Tokens are case-insensitive and normalized to upper case. Broker-native symbol names are required; no symbol substitution is performed.

### Validation examples

```text
GET .../snapshot/EURUSDm?timeframes=                 -> 400 INVALID_REQUEST
GET .../snapshot/EURUSDm?timeframes=M1,,H1           -> 400 INVALID_REQUEST
GET .../snapshot/EURUSDm?timeframes=M1,M1            -> 400 INVALID_REQUEST
GET .../snapshot/EURUSDm?timeframes=NOT_A_TF         -> 400 INVALID_TIMEFRAME
GET .../snapshot/EURUSDm?timeframes=M1,M5,M15,M30,H1,H4,D1,W1,MN1 -> 400 INVALID_REQUEST
GET .../snapshot/EURUSDm?limit=501                   -> 400 INVALID_REQUEST
GET .../snapshot/EURUSDm?limit=5001                  -> 422 VALIDATION_ERROR
GET .../snapshot/DOES_NOT_EXIST                      -> 404 SYMBOL_NOT_FOUND
GET .../snapshot/EURUSDm (no key)                    -> 401 UNAUTHORIZED
```

One `HistoryLimiter` slot covers the whole request, so a burst of snapshots is rate-limited like other history endpoints (`429 RATE_LIMITED`).

## 2. Response envelope

The endpoint uses the standard envelope and request-ID middleware.

```json
{
  "success": true,
  "data": {
    "snapshot_id": "…",
    "symbol": "XAUUSDm",
    "generated_at": "2026-11-27T12:00:00Z",
    "snapshot_status": "COMPLETE",
    "synchronization": {},
    "quote": {},
    "timeframes": {},
    "alignment": {},
    "session": {},
    "data_quality": {},
    "warnings": [],
    "meta": {}
  },
  "meta": { "request_id": "…" }
}
```

`data.meta.request_id` is also populated so the payload is self-contained when stored. Errors use the existing `success: false` / `error.code` / `meta.request_id` shape.

### Top-level fields

| Field | Meaning |
|---|---|
| `snapshot_id` | Unique identifier for this assembled response. |
| `symbol` | Exact broker-native symbol that was resolved. |
| `generated_at` | UTC reference time captured at the start of the build. |
| `snapshot_status` | `COMPLETE`, `PARTIAL`, or `UNAVAILABLE`. |
| `synchronization` | Retrieval timing, quote freshness, candle close-time note, and timestamps. |
| `quote` | Phase 18 quote context (bid/ask/mid/spread/freshness/source). |
| `timeframes` | Map keyed by normalized timeframe. Each entry has metadata, candles, context, quality, status, and optional error. |
| `alignment` | Descriptive cross-timeframe agreement counts and conflicts. |
| `session` | Approximate session context using Phase 18 session rules. |
| `data_quality` | Snapshot-level completeness/reliability score and warnings. |
| `warnings` | Deduplicated warnings from retrieval, quality, and synchronization. |
| `meta` | Request ID, engine versions, flags, and disclaimer. |

### Per-timeframe entry

```json
{
  "M1": {
    "status": "OK",
    "metadata": {
      "timeframe": "M1",
      "duration_seconds": 60,
      "requested_candles": 100,
      "returned_candles": 100,
      "available_candles": 100,
      "latest_candle_time": "2026-11-27T12:00:00Z",
      "latest_candle_epoch": 1764244800,
      "latest_candle_completion": "COMPLETED",
      "data_availability": true,
      "freshness": "CURRENT",
      "role": "detailed execution context",
      "retrieved_at": "2026-11-27T12:00:00Z",
      "retrieval_duration_ms": 12.4
    },
    "candles": [],
    "context": {},
    "data_quality": {}
  }
}
```

`status` is `OK` or `ERROR`. On `ERROR`, the entry also has an `error` object with `code` and a non-leaking message, and the build continues with the remaining timeframes. `latest_candle_completion` is `COMPLETED`, `FORMING`, or `UNKNOWN`; forming bars are always preserved as forming and are ignored by Phase 18 analytics.

Role labels are informational conventions only (for example `D1` = daily macro context, `H4` = major structure, `M1` = detailed execution context). They do not force a decision.

## 3. Snapshot status and data quality

- `COMPLETE` — every timeframe was retrieved, has candles, and the overall snapshot is usable.
- `PARTIAL` — at least one timeframe is available, but a timeframe failed, returned no candles, or the overall quality is unusable.
- `UNAVAILABLE` — no timeframe could be retrieved.

Overall quality reuses Phase 18 grade thresholds. It is the mean Phase 18 per-timeframe score scaled by `available / requested`, with explicit checks for MT5 connection, quote availability/freshness, and timeframe usability. A fresh quote cannot compensate for a disconnected terminal, an unusable quote, or an unusable timeframe. When overall quality is unusable, all descriptive classifications are forced to `UNKNOWN` with `suppressed_reason: "Data quality is unusable"`.

The quality score is **not** a win probability, forecast, or buy/sell confidence.

## 4. Cross-timeframe alignment

Alignment uses the `trend.classification` already produced by Phase 18 for each successful timeframe. Missing, failed, or unknown timeframes are counted as `UNKNOWN`; they are never guessed.

| Field | Meaning |
|---|---|
| `bullish_count`, `bearish_count`, `neutral_count`, `mixed_count`, `unknown_count` | Counts of normalized directions. |
| `analyzed_count` | Counts excluding `UNKNOWN`. |
| `directional_count` | Bullish + bearish only. |
| `alignment_ratio` | Largest analyzed count divided by `analyzed_count`; `null` when nothing is analyzable. |
| `dominant_classification` | Most frequent analyzed class. |
| `conflicts` | Opposite-direction pairs on adjacent available timeframes after sorting by duration (higher timeframe first). |
| `classification` | `FULLY_ALIGNED`, `MOSTLY_ALIGNED`, `MIXED`, `CONFLICTING`, `UNKNOWN`, or `INSUFFICIENT_DATA`. |

Classification order:

1. `INSUFFICIENT_DATA` when fewer than `SNAPSHOT_MIN_ALIGNED_TIMEFRAMES` timeframes are analyzable.
2. `UNKNOWN` when no directional classification is available.
3. `CONFLICTING` when conflicts exist and `alignment_ratio` is below `SNAPSHOT_ALIGNMENT_DOMINANT_RATIO`.
4. `FULLY_ALIGNED` when all analyzable classifications share one direction.
5. `MOSTLY_ALIGNED` when a direction reaches the dominant ratio.
6. `MIXED` otherwise.

Higher timeframes are **not** assumed to be correct. The ratio measures agreement among descriptive classifications, not trade success.

## 5. Synchronization

Three timing concepts are reported separately:

1. **Candle time alignment** — latest candle epoch per timeframe. Different timeframes close at naturally different UTC times; this is not an error.
2. **Retrieval synchronization** — spread between per-timeframe retrieval timestamps and the quote retrieval timestamp.
3. **Quote freshness** — `FRESH`, `STALE`, `VERY_STALE`, or `UNAVAILABLE` from Phase 18.

Retrieval classification:

| Classification | Meaning |
|---|---|
| `SYNCHRONIZED` | Retrieval span within `SNAPSHOT_SYNC_TIGHT_SECONDS` and a fresh quote. |
| `ACCEPTABLE` | Span within `SNAPSHOT_ACCEPTABLE_SYNC_SECONDS`. |
| `PARTIALLY_SYNCHRONIZED` | Missing/failed timeframe but remaining timestamps within tolerance. |
| `UNSYNCHRONIZED` | Span outside acceptable tolerance or quote `VERY_STALE`/`UNAVAILABLE`. |
| `UNKNOWN` | No retrieval timestamps. |

The response never claims that all timeframes were fetched at the same instant. Warnings make cache reuse, stale quotes, missing timeframes, and tight-tolerance overruns explicit.

## 6. Configuration

All settings are additive and documented in `.env.example`. Defaults are safe for an Intel i3 / 8 GB Windows host.

| Variable | Default | Purpose |
|---|---:|---|
| `SNAPSHOT_DEFAULT_TIMEFRAMES` | `M1,M5,M15,H1,H4,D1` | Default timeframe list. |
| `SNAPSHOT_MAX_TIMEFRAMES` | `7` | Hard ceiling per request. |
| `SNAPSHOT_DEFAULT_LIMIT` | `100` | Default candles requested per timeframe. |
| `SNAPSHOT_MAX_LIMIT` | `500` | Hard ceiling for `limit`. |
| `SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME` | `100` | Maximum candles returned per timeframe. |
| `SNAPSHOT_SYNC_TIGHT_SECONDS` | `2` | Tight retrieval tolerance. |
| `SNAPSHOT_ACCEPTABLE_SYNC_SECONDS` | `10` | Acceptable retrieval tolerance. |
| `SNAPSHOT_CANDLE_FRESH_CURRENT_MULTIPLIER` | `2` | Current-candle age threshold in timeframe durations. |
| `SNAPSHOT_CANDLE_FRESH_STALE_MULTIPLIER` | `5` | Lagging/stale candle threshold. |
| `SNAPSHOT_MIN_ALIGNED_TIMEFRAMES` | `2` | Minimum analyzable timeframes for alignment. |
| `SNAPSHOT_ALIGNMENT_DOMINANT_RATIO` | `0.6` | Dominant-direction threshold. |
| `SNAPSHOT_CONFLICT_REVERSALS` | `1` | Conflicts required before `CONFLICTING`. |

Validation enforces `SNAPSHOT_DEFAULT_LIMIT <= SNAPSHOT_MAX_LIMIT <= MAX_CANDLE_LIMIT`, `SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME <= SNAPSHOT_MAX_LIMIT`, non-decreasing sync/candle thresholds, and `SNAPSHOT_MIN_ALIGNED_TIMEFRAMES <= SNAPSHOT_MAX_TIMEFRAMES`.

## 7. Performance and resource use

- One worker thread per request via `asyncio.to_thread`; no thread or process per timeframe.
- Timeframes are retrieved sequentially through the existing serialized MT5 manager, reusing Phase 18 TTL caches and single-flight locks.
- One history-limiter slot covers the whole snapshot.
- Candle retrieval and response size are bounded by settings; no heavy dependency, ML model, or write path is introduced.
- `retrieved_at` and quote `source` make cache hits and live reads visible.

## 8. Client access

### Python

```python
from examples.python_client import MT5MarketDataClient

client = MT5MarketDataClient()  # reads API_KEY from environment
snapshot = client.market_snapshot(
    "XAUUSDm",
    timeframes="M15,H1,H4,D1",
    limit=100,
)
print(snapshot["snapshot_status"], snapshot["alignment"]["classification"])
```

### Next.js (server-side only)

```ts
// app/api/snapshot/route.ts — runs on the server.
import { fetchMarketSnapshot } from "@/examples/nextjs_client";

export async function GET() {
  const snapshot = await fetchMarketSnapshot("XAUUSDm", {
    timeframes: "M15,H1,H4,D1",
    limit: 100,
  });
  return Response.json(snapshot);
}
```

Never put the API key in `NEXT_PUBLIC_*` variables or a browser bundle. Browser clients should call your own server-side route; the API key stays in `process.env`.

## 9. Limitations and troubleshooting

- MT5 timestamps are broker wall time; the shared `BrokerClock` converts them to UTC. Configure `MT5_SERVER_TIMEZONE` or `MT5_SERVER_UTC_OFFSET_MINUTES` for reliable history.
- Quote and candles are separate MT5 calls and are not atomic. Use `synchronization` rather than assuming simultaneity.
- A `PARTIAL` snapshot is expected when one timeframe fails or has insufficient history; per-timeframe `error` / `data_quality.missing_data` explains why.
- `UNKNOWN` classifications indicate suppressed or insufficient data, not a neutral market opinion.
- Session windows are approximate and not broker-verified.
- No live MT5 broker verification is claimed by the automated tests. They use the mocked `FakeMT5` surface in `tests/conftest.py`.

| Symptom | Check |
|---|---|
| `401 UNAUTHORIZED` | Header name and key value; rotation with `API_KEY_PREVIOUS`. |
| `429 RATE_LIMITED` | `HISTORY_RATE_LIMIT_PER_MINUTE`, concurrent consumers, retry hint in error details. |
| `404 SYMBOL_NOT_FOUND` | Exact broker-native symbol including suffix. |
| `400` on `timeframes` | Empty entries, duplicates, unsupported tokens, or more than `SNAPSHOT_MAX_TIMEFRAMES`. |
| `PARTIAL` with `ERROR` timeframe | Per-timeframe `error.code` and `warnings`. |
| `VERY_STALE` quote | Broker clock configuration, collector health, `QUOTE_STALE_MULTIPLIER`. |
| All classifications `UNKNOWN` | `data_quality.usable`, `suppressed_reason`, and per-timeframe completed-candle counts. |
