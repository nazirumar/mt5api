"""Orchestration service for the deterministic market context engine.

This service is synchronous on purpose: the REST layer calls it through
``asyncio.to_thread``. It uses only the existing MT5 services, a short-lived TTL
cache, and per-key single-flight locks so repeated requests do not multiply
serialized MT5 calls on the low-resource host.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..core.exceptions import InvalidRequestError, SymbolNotFoundError
from ..core.helpers import timestamp_to_iso
from ..mt5.timeframes import normalize_timeframe
from ..services.cache import refresh_freshness
from . import analytics
from .sessions import session_context


class MarketContextEngine:
    """Build a market snapshot from real MT5 data without fabricating values."""

    def __init__(
        self,
        settings: Any,
        manager: Any,
        symbols: Any,
        quotes: Any,
        candles: Any,
        cache: Any,
        clock: Any,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.symbols = symbols
        self.quotes = quotes
        self.candles = candles
        self.cache = cache
        self.clock = clock
        self.params = analytics.settings_to_params(settings)
        self._cache_lock = threading.Lock()
        self._info_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._candle_cache: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}
        self._key_locks: dict[Any, threading.Lock] = {}

    def _param(self, name: str) -> Any:
        return self.params.get(name, analytics.DEFAULT_PARAMS.get(name))

    def _lock_for(self, key: Any) -> threading.Lock:
        with self._cache_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                if len(self._key_locks) > 1000:
                    self._key_locks.clear()
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _symbol_info(self, symbol: str) -> dict[str, Any]:
        key = symbol.strip()
        if not key:
            raise InvalidRequestError("symbol must not be empty")
        ttl = float(self._param("CONTEXT_SYMBOL_INFO_CACHE_SECONDS") or 300.0)
        cached = self._info_cache.get(key)
        if cached and time.monotonic() - cached[0] < ttl:
            return dict(cached[1])

        with self._lock_for(("symbol", key)):
            cached = self._info_cache.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return dict(cached[1])
            raw = self.manager.call("symbol_info", key, allow_none=True)
            if raw is None:
                raise SymbolNotFoundError(f"Symbol '{key}' was not found on this broker")
            info = dict(self.symbols.symbol_info(raw))
            self._info_cache[key] = (time.monotonic(), info)
            return dict(info)

    def _freshness(self, quote: dict[str, Any], now_epoch: int) -> str:
        if not quote.get("market_available") or quote.get("bid") is None or quote.get("ask") is None:
            return "UNAVAILABLE"
        age = quote.get("age_seconds")
        if age is None:
            return "UNAVAILABLE"
        stale_after = float(quote.get("stale_after_seconds") or 0)
        if stale_after <= 0:
            stale_after = float(getattr(self.settings, "MARKET_POLL_INTERVAL", 0.5)) * float(
                getattr(self.settings, "QUOTE_STALE_MULTIPLIER", 3.0)
            )
        age = max(float(age), 0.0)
        if age <= stale_after:
            return "FRESH"
        if age <= stale_after * float(self._param("CONTEXT_QUOTE_VERY_STALE_MULTIPLIER")):
            return "STALE"
        return "VERY_STALE"

    def _quote(self, symbol: str, info: dict[str, Any], now_epoch: int) -> tuple[dict[str, Any], str]:
        stale_after = float(getattr(self.settings, "MARKET_POLL_INTERVAL", 0.5)) * float(
            getattr(self.settings, "QUOTE_STALE_MULTIPLIER", 3.0)
        )
        cached = self.cache.get(symbol) if self.cache is not None else None
        if cached:
            refreshed = refresh_freshness(cached, stale_after, now_epoch)
            if self._freshness(refreshed, now_epoch) == "FRESH":
                return refreshed, "cache"
        quote = dict(self.quotes.get_quote(symbol, info=info))
        if self.cache is not None and quote.get("market_available"):
            self.cache.set(quote)
        return quote, "mt5_live"

    def _quote_context(self, quote: dict[str, Any], info: dict[str, Any], source: str, now_epoch: int) -> dict[str, Any]:
        refreshed = refresh_freshness(quote, float(quote.get("stale_after_seconds") or 0), now_epoch)
        bid, ask = refreshed.get("bid"), refreshed.get("ask")
        mid = (float(bid) + float(ask)) / 2.0 if bid is not None and ask is not None else None
        point = float(info.get("point") or 0)
        spread = refreshed.get("spread")
        spread_points = refreshed.get("spread_points")
        if spread_points is None and spread is not None and point > 0:
            spread_points = round(float(spread) / point, 2)
        freshness = self._freshness(refreshed, now_epoch)
        return {
            "symbol": quote.get("symbol") or info.get("symbol"),
            "bid": bid,
            "ask": ask,
            "mid": round(mid, 10) if mid is not None else None,
            "spread": spread,
            "spread_points": spread_points,
            "point": point if point > 0 else None,
            "digits": int(info.get("digits") or 0) or None,
            "tick_size": float(info.get("tick_size") or 0) or None,
            "tick_value": float(info.get("tick_value") or 0) or None,
            "timestamp": refreshed.get("timestamp"),
            "age_seconds": refreshed.get("age_seconds"),
            "stale_after_seconds": refreshed.get("stale_after_seconds"),
            "freshness": freshness,
            "market_available": bool(refreshed.get("market_available")),
            "status": refreshed.get("status", "unavailable"),
            "source": source,
            "symbol_category": info.get("category", "other"),
        }

    def _series(self, symbol: str, timeframe: str, lookback: int, info: dict[str, Any]) -> list[dict[str, Any]]:
        key = (symbol, timeframe, lookback)
        ttl = float(self._param("CONTEXT_CANDLE_CACHE_SECONDS") or 30.0)
        cached = self._candle_cache.get(key)
        if cached and time.monotonic() - cached[0] < ttl:
            return [dict(item) for item in cached[1]]

        with self._lock_for(("candles", key)):
            cached = self._candle_cache.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return [dict(item) for item in cached[1]]
            result = self.candles.get_candles(symbol, timeframe, limit=lookback, info=info)
            series = [dict(item) for item in result.get("candles", [])]
            self._candle_cache[key] = (time.monotonic(), series)
            return [dict(item) for item in series]

    @staticmethod
    def _indicator_summary(
        series: list[dict[str, Any]], include_indicators: bool, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not include_indicators:
            return {"included": False}
        closes = [
            float(item["close"]) for item in series
            if item.get("close") is not None and analytics._as_float(item.get("close")) is not None
        ]
        period = int(params.get("CONTEXT_ATR_PERIOD", 14))
        current_atr = analytics.atr(series, period)
        return {
            "included": True,
            "atr": analytics._round(current_atr),
            "atr_percent": analytics._round(current_atr / closes[-1] * 100.0 if current_atr is not None and closes and closes[-1] else None),
            "sma_fast": analytics._round(analytics.sma(closes, int(params.get("CONTEXT_TREND_FAST_SMA", 10)))),
            "sma_slow": analytics._round(analytics.sma(closes, int(params.get("CONTEXT_TREND_SLOW_SMA", 30)))),
            "candles_used": len(series),
        }

    def _timeframe_context(
        self,
        series: list[dict[str, Any]],
        quote: dict[str, Any],
        include_indicators: bool,
        include_structure: bool,
        include_candles: bool,
    ) -> dict[str, Any]:
        trend = analytics.analyze_trend(series, self.params)
        regime = analytics.analyze_regime(series, self.params)
        momentum = analytics.analyze_momentum(series, self.params)
        volatility = analytics.analyze_volatility(series, self.params)
        position = analytics.analyze_price_position(series, quote.get("mid") or quote.get("bid"), self.params)
        spread = analytics.analyze_spread(quote, series, self.params)
        structure = (
            analytics.analyze_candle_structure(series, self.params)
            if include_structure else
            {"latest": None, "recent": [], "summary": {"included": False}, "method": "Candle structure not requested"}
        )
        context: dict[str, Any] = {
            "trend": trend,
            "regime": regime,
            "momentum": momentum,
            "volatility": volatility,
            "price_position": position,
            "spread": spread,
            "candle_structure": structure,
            "indicators": self._indicator_summary(series, include_indicators, self.params),
            "data_sufficiency": bool(trend.get("data_sufficiency") and volatility.get("data_sufficiency")),
            "method": "deterministic_rule_set_v1",
        }
        if include_candles:
            limit = int(self._param("CONTEXT_MAX_RETURN_CANDLES"))
            context["candles"] = [dict(item) for item in series[-limit:]]
        return context

    def _connected(self) -> bool:
        try:
            return bool(self.manager.is_connected())
        except Exception:
            return False

    def _broker_time(self) -> str | None:
        try:
            broker_now = self.clock.to_broker_datetime(datetime.now(timezone.utc))
            text = broker_now.replace(tzinfo=None).isoformat(timespec="seconds")
            description = self.clock.describe()
            zone = description.get("timezone")
            return f"{text} ({zone})" if zone else f"{text} (UTC offset {description.get('utc_offset_minutes')} min)"
        except Exception:
            return None

    # Public, additive accessors used by the Phase 19 snapshot engine. They only
    # delegate to the helpers above so both phases share one cache and one set
    # of calculations; the /market/context response is unchanged.
    def resolve_symbol(self, symbol: str) -> dict[str, Any]:
        """Resolve an exact broker-native symbol using the shared TTL cache."""
        return self._symbol_info(symbol)

    def quote_snapshot(
        self,
        symbol: str,
        info: dict[str, Any] | None = None,
        now_epoch: int | None = None,
    ) -> dict[str, Any]:
        """Return one normalized quote context using the shared quote cache."""
        details = dict(info) if info is not None else self._symbol_info(symbol)
        reference = int(now_epoch) if now_epoch is not None else int(datetime.now(timezone.utc).timestamp())
        raw, source = self._quote(details["symbol"], details, reference)
        return self._quote_context(raw, details, source, reference)

    def series(
        self, symbol: str, timeframe: str, limit: int, info: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return a candle series using the shared TTL cache and single-flight."""
        return self._series(symbol, normalize_timeframe(timeframe), int(limit), dict(info))

    def context_for_series(
        self,
        series: list[dict[str, Any]],
        quote: dict[str, Any],
        include_indicators: bool = True,
        include_structure: bool = True,
        include_candles: bool = False,
    ) -> dict[str, Any]:
        """Build deterministic context from already-retrieved candles."""
        return self._timeframe_context(
            [dict(item) for item in series],
            dict(quote),
            bool(include_indicators),
            bool(include_structure),
            bool(include_candles),
        )

    def quality_for_series(
        self,
        info: dict[str, Any],
        quote: dict[str, Any],
        series: list[dict[str, Any]],
        connected: bool,
    ) -> dict[str, Any]:
        """Score a timeframe snapshot with the shared Phase 18 quality rules."""
        return analytics.assess_data_quality(
            dict(info), dict(quote), [dict(item) for item in series], bool(connected), self.params
        )

    def connected(self) -> bool:
        """Return the current MT5 connection state without raising."""
        return self._connected()

    def broker_time(self) -> str | None:
        """Return a descriptive broker wall-clock string when available."""
        return self._broker_time()

    @staticmethod
    def _normalized_timeframes(
        timeframe: str | None, timeframes: str | None, max_timeframes: int
    ) -> tuple[str, list[str]]:
        explicit = [item.strip() for item in (timeframes or "").split(",") if item.strip()]
        if explicit:
            normalized = [normalize_timeframe(item) for item in explicit]
        else:
            normalized = [normalize_timeframe(timeframe or "M5")]
        unique: list[str] = []
        for item in normalized:
            if item not in unique:
                unique.append(item)
        if len(unique) > max_timeframes:
            raise InvalidRequestError(
                f"A maximum of {max_timeframes} timeframes is supported",
                details={"max_timeframes": max_timeframes},
            )
        return unique[0], unique

    def build_context(
        self,
        symbol: str,
        timeframe: str | None = None,
        timeframes: str | None = None,
        lookback: int | None = None,
        include_candles: bool = False,
        include_indicators: bool = True,
        include_structure: bool = True,
        request_id: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        now_epoch = int(current_utc.timestamp())

        wanted_lookback = int(lookback or getattr(self.settings, "CONTEXT_DEFAULT_LOOKBACK", 100))
        max_lookback = int(getattr(self.settings, "CONTEXT_MAX_LOOKBACK", 500))
        if wanted_lookback <= 0 or wanted_lookback > max_lookback:
            raise InvalidRequestError(
                f"lookback must be between 1 and {max_lookback}",
                details={"max_lookback": max_lookback},
            )
        primary, requested = self._normalized_timeframes(
            timeframe, timeframes, int(getattr(self.settings, "CONTEXT_MAX_TIMEFRAMES", 4))
        )

        info = self._symbol_info(symbol)
        quote_raw, quote_source = self._quote(info["symbol"], info, now_epoch)
        quote = self._quote_context(quote_raw, info, quote_source, now_epoch)

        contexts: dict[str, Any] = {}
        series_by_timeframe: dict[str, list[dict[str, Any]]] = {}
        for tf in requested:
            series = self._series(info["symbol"], tf, wanted_lookback, info)
            series_by_timeframe[tf] = series
            contexts[tf] = self._timeframe_context(
                series, quote, include_indicators, include_structure, include_candles
            )

        connected = self._connected()
        quality = analytics.assess_data_quality(
            info, quote, series_by_timeframe[primary], connected, self.params
        )
        top_context = dict(contexts[primary])
        top_context.pop("candles", None)
        session = session_context(current_utc, self._broker_time(), self.params)
        top_context["session"] = session
        if not quality["usable"]:
            top_context = analytics.unknown_classifications(top_context)
            contexts = {tf: analytics.unknown_classifications(value) for tf, value in contexts.items()}

        return {
            "symbol": info["symbol"],
            "generated_at": timestamp_to_iso(now_epoch),
            "market_status": "AVAILABLE" if quality["usable"] and quote["market_available"] else "UNAVAILABLE",
            "quote": quote,
            "context": top_context,
            "data_quality": quality,
            "timeframes": contexts,
            "session": session,
            "meta": {
                "request_id": request_id,
                "engine_version": "1.0.0",
                "method": "deterministic_rule_set_v1",
                "timeframe": primary,
                "timeframes_requested": requested,
                "lookback": wanted_lookback,
                "include_candles": bool(include_candles),
                "include_indicators": bool(include_indicators),
                "include_structure": bool(include_structure),
                "disclaimer": (
                    "Context classifications are descriptive measurements of past and current data, "
                    "not trading signals, guaranteed outcomes, or future price predictions."
                ),
            },
        }
