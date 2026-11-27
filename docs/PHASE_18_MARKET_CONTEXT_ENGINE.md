# Phase 18 — AI Market Context Engine

**Status:** implemented on top of the existing read-only MT5 market-data service.  
**Baseline:** `e2f23f0`  
**Design document:** `docs/PHASE_18_IMPLEMENTATION_PLAN.md`

The market context engine converts existing MT5 quote and candle data into a
standardized, deterministic, machine-readable snapshot. It is a rule engine,
not a machine-learning model. It does not place, modify, or close trades, and
it never invents prices, candles, quotes, or analysis values.

> **Important:** Every classification is descriptive context derived from past
> and current data. It is not a trading signal, win probability, forecast, or
> guarantee of any outcome.

---

## 1. Endpoint

### Request

```http
GET /api/v1/market/context/{symbol}
X-API-Key: <your-api-key>
```

The route uses the existing router-level API-key dependency, request-ID
middleware, error envelope, and history limiter. REST authentication is
header-only; `?api_key=` is WebSocket-only and is intentionally not accepted
here.

| Query parameter | Default | Constraints | Purpose |
|---|---:|---|---|
| `timeframe` | `CONTEXT_DEFAULT_TIMEFRAME` (`M5`) | Any supported MT5 timeframe | Primary analysis timeframe |
| `timeframes` | unset | Max `CONTEXT_MAX_TIMEFRAMES` (4) | Comma-separated timeframes, e.g. `M5,M15,H1` |
| `lookback` | `CONTEXT_DEFAULT_LOOKBACK` (100) | 1..`CONTEXT_MAX_LOOKBACK` (500) | Requested completed candles per timeframe |
| `include_candles` | `false` | boolean | Include up to `CONTEXT_MAX_RETURN_CANDLES` candles per timeframe |
| `include_indicators` | `true` | boolean | Include numeric indicator measurements |
| `include_structure` | `true` | boolean | Include candle-geometry data |

When `timeframes` is supplied, its first value becomes the primary timeframe
used for the top-level `context`. Each requested timeframe is calculated
independently. One history-limiter slot covers the entire request, so
multi-timeframe requests cannot multiply the per-client history allowance.

### Examples

```bash
curl -s \
  -H "X-API-Key: $MT5_API_KEY" \
  "http://127.0.0.1:8080/api/v1/market/context/XAUUSDm?timeframe=M5&lookback=100"

curl -s \
  -H "X-API-Key: $MT5_API_KEY" \
  "http://127.0.0.1:8080/api/v1/market/context/EURUSDm?timeframes=M1,M5,H1&lookback=80&include_candles=true"
```

```python
import httpx

response = httpx.get(
    "http://127.0.0.1:8080/api/v1/market/context/EURUSDm",
    headers={"X-API-Key": "..."},  # read from your secret store, never frontend code
    params={"timeframe": "M5", "lookback": 100},
    timeout=30.0,
)
response.raise_for_status()
context = response.json()["data"]
```

```ts
const res = await fetch(
  "/api/v1/market/context/XAUUSDm?timeframe=M5&lookback=100",
  { headers: { "X-API-Key": serverSideApiKey }, cache: "no-store" }
);
const envelope = await res.json();
```

Never place the API key in `NEXT_PUBLIC_*` variables, browser bundles, mobile
apps, or public repositories. Call the API from a server-side route or local
agent that reads the key from its own environment/secret store.

---

## 2. Response envelope

The standard project envelope is preserved:

```json
{
  "success": true,
  "data": { "...": "market context payload" },
  "meta": { "request_id": "..." }
}
```

The payload shape below is illustrative; real values are computed only from
the connected broker's live quote and completed candle history. Null values
mean the broker did not supply the value.

```json
{
  "symbol": "XAUUSDm",
  "generated_at": "2026-11-27T22:00:00Z",
  "market_status": "AVAILABLE",
  "quote": {
    "bid": 2350.1,
    "ask": 2350.35,
    "mid": 2350.225,
    "spread": 0.25,
    "spread_points": 25.0,
    "point": 0.01,
    "digits": 2,
    "tick_size": 0.01,
    "tick_value": 1.0,
    "timestamp": "2026-11-27T22:00:00Z",
    "age_seconds": 0.1,
    "freshness": "FRESH",
    "market_available": true,
    "status": "ok",
    "source": "mt5_live",
    "symbol_category": "metal"
  },
  "context": {
    "trend": {
      "classification": "BULLISH",
      "strength": 1.0,
      "method": "SMA relationship + regression slope + close consistency + swing structure"
    },
    "regime": {
      "classification": "TRENDING",
      "explanation": "Directional consistency and net movement in ATR units exceed trend thresholds",
      "measurements": {}
    },
    "momentum": { "classification": "STRONG_BULLISH", "direction": "BULLISH" },
    "volatility": { "classification": "NORMAL", "atr": 0.42, "atr_percent": 0.38 },
    "price_position": { "classification": "UPPER", "range_percent": 82.0 },
    "spread": { "classification": "NORMAL", "current": 0.25 },
    "candle_structure": { "latest": {}, "summary": {} },
    "session": { "classification": "UNKNOWN", "verified": false },
    "indicators": { "included": true, "atr": 0.42 }
  },
  "data_quality": {
    "score": 100,
    "grade": "EXCELLENT",
    "usable": true,
    "warnings": [],
    "missing_data": []
  },
  "timeframes": {
    "M5": { "trend": {}, "candle_structure": {}, "indicators": {} }
  },
  "session": { "classification": "UNKNOWN", "verified": false },
  "meta": {
    "request_id": "...",
    "engine_version": "1.0.0",
    "method": "deterministic_rule_set_v1",
    "disclaimer": "Descriptive context only; not a trading signal."
  }
}
```

Errors use the existing envelope, for example:

```json
{
  "success": false,
  "error": {
    "code": "SYMBOL_NOT_FOUND",
    "message": "Symbol 'XAUUSD' was not found on this broker",
    "details": null
  },
  "meta": { "request_id": "..." }
}
```

Expected codes: `UNAUTHORIZED` (401), `VALIDATION_ERROR` (422),
`SYMBOL_NOT_FOUND` (404), `INVALID_TIMEFRAME` (400), `INVALID_REQUEST` (400),
`RATE_LIMITED` (429), `MT5_NOT_INITIALIZED`/`MT5_UNAVAILABLE` (503), and
`INTERNAL_ERROR` (500).

---

## 3. Quote context and freshness

Quote context preserves the broker-native symbol and uses the symbol's own
`digits`, `point`, `trade_tick_size`, and `trade_tick_value`. Broker suffixes
such as `EURUSDm`, `XAUUSDm`, and `GBPUSD.a` are never stripped for lookup;
normalization is used only for category classification.

- `mid` is `(bid + ask) / 2` when both are present.
- `spread` and `spread_points` come from the existing quote adapter.
- Missing tick or missing price fields produce `null` prices and
  `freshness = "UNAVAILABLE"`; no fallback price is generated.

Freshness uses the quote age and `MARKET_POLL_INTERVAL * QUOTE_STALE_MULTIPLIER`:

| Freshness | Rule |
|---|---|
| `FRESH` | `age <= stale_after_seconds` |
| `STALE` | `stale_after_seconds < age <= stale_after_seconds * CONTEXT_QUOTE_VERY_STALE_MULTIPLIER` |
| `VERY_STALE` | Beyond the very-stale multiplier |
| `UNAVAILABLE` | Missing quote, price, or timestamp |

`VERY_STALE` and `UNAVAILABLE` make the whole snapshot unusable. A `STALE`
quote remains observable but adds a warning.

---

## 4. Classification methodology

All measurements use completed candles only, in ascending UTC timestamp order.
Forming candles are excluded, so no future or incomplete candle can influence a
classification. All thresholds are read from `Settings`/`.env.example`.

### 4.1 Trend

Method: `SMA relationship + regression slope + close consistency + swing structure`.

Four votes are cast, each `-1`, `0`, or `+1`:

1. **SMA:** fast SMA versus slow SMA (`CONTEXT_TREND_FAST_SMA`, `CONTEXT_TREND_SLOW_SMA`).
2. **Slope:** least-squares slope of closes normalized by mean close; votes only
   beyond `CONTEXT_TREND_SLOPE_THRESHOLD`.
3. **Consistency:** dominant close-to-close direction when its ratio reaches
   `CONTEXT_TREND_CONSISTENCY_THRESHOLD`.
4. **Structure:** higher highs/higher lows versus lower highs/lower lows.

The score is the sum of votes (-4..+4):

| Score | Classification |
|---:|---|
| `>= 2` | `BULLISH` |
| `<= -2` | `BEARISH` |
| Mixed positive and negative votes | `MIXED` |
| Otherwise | `NEUTRAL` |
| Fewer than `CONTEXT_MIN_CANDLES` | `UNKNOWN` |

`strength` is `abs(score) / number_of_votes`. This is a measure of agreement
between simple rules, not confidence in future direction.

### 4.2 Regime

Method: `ATR level/expansion + net movement in ATR units + body/range ratio + consistency`.

Inputs: current ATR, prior-half ATR, ATR percentage of last close, ATR expansion
ratio, absolute net movement in ATR units, average candle body/range ratio, and
directional consistency.

Rules are evaluated in this order:

1. `VOLATILE` — ATR percentage >= `CONTEXT_REGIME_VOLATILE_ATR_PERCENT` or ATR
   expansion >= `CONTEXT_REGIME_EXPANSION_RATIO`.
2. `QUIET` — ATR percentage <= `CONTEXT_REGIME_QUIET_ATR_PERCENT` and no range
   expansion above `CONTEXT_REGIME_CONTRACTION_RATIO`.
3. `TRENDING` — consistency >= `CONTEXT_TREND_CONSISTENCY_THRESHOLD` and net
   movement >= `CONTEXT_REGIME_TREND_ATR_MOVE` ATRs.
4. `RANGING` — average body/range <= `CONTEXT_REGIME_RANGING_BODY_RATIO` and
   net movement below the trend threshold.
5. `TRANSITIONAL` — no single rule dominates.
6. `UNKNOWN` — insufficient completed candles or ATR unavailable.

### 4.3 Momentum

Method: close-to-close movement over `CONTEXT_MOMENTUM_LOOKBACK`, normalized by
ATR, with acceleration measured against the previous equal-length window.

| Normalized move | Classification |
|---:|---|
| `>= CONTEXT_MOMENTUM_STRONG_ATR_MULTIPLIER` | `STRONG_BULLISH` |
| `>= CONTEXT_MOMENTUM_WEAK_ATR_MULTIPLIER` | `BULLISH` |
| `< -CONTEXT_MOMENTUM_STRONG_ATR_MULTIPLIER` | `STRONG_BEARISH` |
| `<= -CONTEXT_MOMENTUM_WEAK_ATR_MULTIPLIER` | `BEARISH` |
| Otherwise | `NEUTRAL` |
| Insufficient history | `UNKNOWN` |

`acceleration_state` is `ACCELERATING`, `DECELERATING`, `STABLE`, or `UNKNOWN`.
Momentum describes recent completed-candle behavior only; it is never a
prediction of future price direction.

### 4.4 Volatility

ATR is the mean true range over the last `CONTEXT_ATR_PERIOD` true ranges:

```text
TR = max(high - low, |high - previous_close|, |low - previous_close|)
ATR = mean(last N TR values)
ATR% = ATR / last_close * 100
```

Classification uses strictly increasing ATR-percentage bands:

| ATR% | Classification |
|---:|---|
| `>= CONTEXT_VOLATILITY_VERY_HIGH_ATR_PERCENT` | `VERY_HIGH` |
| `>= CONTEXT_VOLATILITY_HIGH_ATR_PERCENT` | `HIGH` |
| `<= CONTEXT_VOLATILITY_VERY_LOW_ATR_PERCENT` | `VERY_LOW` |
| `<= CONTEXT_VOLATILITY_LOW_ATR_PERCENT` | `LOW` |
| Otherwise | `NORMAL` |

Additional measurements: current candle range, average candle range, relative
volatility (current/average range), ATR expansion ratio, and the percentile rank
of the latest range against `CONTEXT_VOLATILITY_PERCENTILE_WINDOW` prior ranges.

### 4.5 Candle structure

For each of the last `CONTEXT_CANDLE_STRUCTURE_LIMIT` completed candles:

```text
body = |close - open|
upper_wick = high - max(open, close)
lower_wick = min(open, close) - low
range = high - low
body_to_range_ratio = body / range
```

Explicit patterns are emitted only when the ratio rules match:

| Pattern | Rule |
|---|---|
| `DOJI_LIKE` | body ratio <= `CONTEXT_CANDLE_DOJI_RATIO` |
| `SMALL_BODY` | body ratio <= `CONTEXT_CANDLE_SMALL_BODY_RATIO` |
| `LONG_UPPER_WICK` | upper-wick ratio >= `CONTEXT_CANDLE_WICK_RATIO` |
| `LONG_LOWER_WICK` | lower-wick ratio >= `CONTEXT_CANDLE_WICK_RATIO` |
| `STRONG_BULLISH_CANDLE` / `STRONG_BEARISH_CANDLE` | body ratio >= `CONTEXT_CANDLE_STRONG_BODY_RATIO` with matching direction |
| `NORMAL` | no other rule |

Patterns are geometry labels, not predictive signals.

### 4.6 Price position

Using the last `CONTEXT_PRICE_POSITION_LOOKBACK` completed candles:

```text
range_percent = clamp((current_price - recent_low) / (recent_high - recent_low) * 100, 0, 100)
```

Zones: `LOWER` at or below `CONTEXT_PRICE_POSITION_LOWER_ZONE_PERCENT`, `UPPER`
at or above `CONTEXT_PRICE_POSITION_UPPER_ZONE_PERCENT`, otherwise `MIDDLE`.
The response includes a neutral description such as "Price is currently located
in the upper 25% of the selected lookback range." No buy/sell label is emitted.

### 4.7 Spread intelligence

Spread is normalized per symbol in points; EURUSD and XAUUSD absolute price
spreads are never compared directly. Current spread points come from the live
quote. Historical observations come from MT5's per-candle spread field over
`CONTEXT_SPREAD_LOOKBACK` completed candles.

| Current / recent-average points | Classification |
|---:|---|
| `>= CONTEXT_SPREAD_ABNORMAL_RATIO` | `ABNORMAL` |
| `>= CONTEXT_SPREAD_ELEVATED_RATIO` | `ELEVATED` |
| Otherwise | `NORMAL` |
| Missing current or history | `UNKNOWN` |

### 4.8 Market session

The existing approximate UTC windows are reused and moved to a shared,
tested module without changing `/api/v1/market/sessions`. `CONTEXT_SESSION_TIMEZONE`
is an IANA timezone, so DST transitions are handled by `zoneinfo`. Operators may
override windows with `CONTEXT_SESSION_WINDOWS`, e.g.
`London=7-16,New York=12-21,Sydney=21-6`.

The result includes current UTC time, an independently reported broker time,
active sessions, London/New York/Asian/Sydney states, London-New York overlap,
and the next approximate transition. `classification` is always `UNKNOWN` and
`verified=false`: the current broker adapter does not expose reliable symbol
trading hours, holidays, or DST rules. Broker server time is never assumed to
equal UTC.

---

## 5. Data quality and usability

The score describes only the reliability and completeness of the snapshot. It
is **not** a probability of winning a trade, prediction confidence, accuracy
forecast, or buy/sell confidence.

| Check | Weight |
|---|---:|
| Broker symbol metadata available | 10 |
| Live quote with bid and ask | 15 |
| Quote `FRESH` | 15 |
| At least `CONTEXT_MIN_CANDLES` candles | 20 |
| Valid OHLC on all returned candles | 15 |
| Strictly increasing, unique candle epochs | 10 |
| Spread observation available | 5 |
| MT5 terminal connected | 10 |

Grades: `EXCELLENT` >= 90, `GOOD` >= 75, `FAIR` >= `CONTEXT_DATA_QUALITY_MIN_SCORE`,
`POOR` below that, and `UNUSABLE` when any essential check fails. Essential
failures include missing symbol, missing quote, insufficient candles, invalid
OHLC, `VERY_STALE`/`UNAVAILABLE` quote freshness, or a disconnected terminal.
When unusable, all descriptive classifications are forced to `UNKNOWN` and
`market_status` becomes `UNAVAILABLE`.

---

## 6. Performance and architecture

- No new MT5 connection, collector, worker pool, model, or dependency is added.
- All MT5 access remains inside `MT5ConnectionManager.call()` and is offloaded
  from the event loop with `asyncio.to_thread`.
- Symbol info gets a TTL cache (`CONTEXT_SYMBOL_INFO_CACHE_SECONDS`); candle
  series get a TTL cache (`CONTEXT_CANDLE_CACHE_SECONDS`).
- Per-key single-flight locks prevent concurrent duplicate lookups.
- A fresh cached quote is reused; stale/missing quotes trigger one live quote
  call. Quote and candle calls reuse the same symbol-info snapshot.
- One `HistoryLimiter` slot covers all requested timeframes.
- Returned candles are capped by `CONTEXT_MAX_RETURN_CANDLES`.
- No catalog scan is performed on the normal context path.

---

## 7. Limitations

- Deterministic rules describe current/past conditions; they do not predict
  future prices or trade outcomes.
- Session hours are approximate and not broker-verified.
- Spread history is in-process and starts empty after restart; candle spread
  values depend on the broker's MT5 rate-record conventions.
- Point, digits, tick size, and tick value can be `null` when the broker does
  not supply them.
- History is limited by existing `MAX_CANDLE_LIMIT`, `MAX_CANDLE_RANGE_DAYS`,
  and the history rate limiter.
- Mock tests verify rule behavior and API plumbing only; they do not prove live
  broker-data correctness. Real terminal/broker verification must be recorded
  separately.

---

## 8. Troubleshooting

| Symptom | Action |
|---|---|
| `401 UNAUTHORIZED` | Send the secret in `X-API-Key`; REST query-string keys are not supported. |
| `404 SYMBOL_NOT_FOUND` | Use the exact broker-native symbol from `/api/v1/symbols`, including suffixes. |
| `400 INVALID_TIMEFRAME` | Use a supported timeframe from `TIMEFRAME_SECONDS` (M1, M5, M15, H1, H4, D1, etc.). |
| `400 INVALID_REQUEST` | Reduce `lookback` or `timeframes` to configured bounds. |
| `429 RATE_LIMITED` | Respect `retry_after_seconds`; cache results client-side. |
| `VERY_STALE` quote | Check terminal connection, market hours, and `MARKET_POLL_INTERVAL`. |
| `UNUSABLE` quality | Inspect `data_quality.missing_data` and `checks`; do not interpret `UNKNOWN` as bearish/neutral. |
| Unexpected session state | Check `CONTEXT_SESSION_TIMEZONE` and confirm approximate windows with the broker. |
| All classifications `UNKNOWN` | Usually a suppressed snapshot due to unusable data quality; restore data first. |
