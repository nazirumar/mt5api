"""One shared quote/candle polling loop for REST cache and WebSocket clients."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core.helpers import timestamp_to_iso
from ..core.logging import get_logger
from ..database.repositories import record_collector_stat, record_event
from ..mt5.candles import CandleService
from ..mt5.connection import MT5ConnectionManager
from ..mt5.quotes import QuoteService
from ..mt5.symbols import SymbolService
from ..mt5.timeframes import TIMEFRAME_SECONDS
from .cache import QuoteCache
from .candle_cache import CandleCache
from .subscription_manager import SubscriptionManager

logger = get_logger("nazbeen_mt5.services.collector")


class MarketCollector:
    def __init__(
        self,
        settings: Any,
        manager: MT5ConnectionManager,
        quote_service: QuoteService,
        candle_service: CandleService,
        symbol_service: SymbolService,
        cache: QuoteCache,
        candle_cache: CandleCache,
        subscriptions: SubscriptionManager,
        session_factory: Any,
    ):
        self.settings = settings
        self.manager = manager
        self.quote_service = quote_service
        self.candle_service = candle_service
        self.symbol_service = symbol_service
        self.cache = cache
        self.candle_cache = candle_cache
        self.subscriptions = subscriptions
        self.session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._default_symbols: list[str] = []
        self._poll_count = 0
        self._last_poll_at: str | None = None
        self._last_error: str | None = None
        self._last_latency_ms = 0.0
        # Wall bucket throttles expensive history calls; epoch prevents a
        # repeated candle from being emitted across a bucket boundary.
        self._last_candle_bucket: dict[tuple[str, str], int] = {}
        self._last_candle_epoch: dict[tuple[str, str], int] = {}
        self._candle_events = 0

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "poll_count": self._poll_count,
            "last_poll_at": self._last_poll_at,
            "last_latency_ms": round(self._last_latency_ms, 2),
            "last_error": self._last_error,
            "default_symbols": list(self._default_symbols),
            "candle_events": self._candle_events,
            "candle_pairs": len(self._last_candle_epoch),
        }

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="mt5-market-collector")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _discover_default_symbols(self) -> list[str]:
        """Pick a small broker-native watchlist; never rewrite symbol names."""
        if self.settings.WATCHED_SYMBOLS:
            return list(self.settings.WATCHED_SYMBOLS)
        if self._default_symbols:
            return self._default_symbols
        try:
            catalog = await asyncio.to_thread(self.symbol_service.list_symbols)
            # Exact broker-native names first, then prefixed variants
            # (e.g. EURUSDm before XAUAUD when looking for gold).
            preferred = ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "XAGUSD", "US30", "BTCUSD")
            chosen: list[str] = []
            for exact in preferred:
                for item in catalog:
                    if item["symbol"].upper() == exact and item["symbol"] not in chosen:
                        chosen.append(item["symbol"])
                        break
            for prefix in ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "XAGUSD", "US30", "BTC"):
                if len(chosen) >= 6:
                    break
                for item in catalog:
                    if item["symbol"].upper().startswith(prefix) and item["symbol"] not in chosen:
                        chosen.append(item["symbol"])
                        break
            self._default_symbols = chosen[:6]
        except Exception as exc:
            logger.warning("Default symbol discovery failed: %s", exc)
            self._default_symbols = []
        return self._default_symbols

    async def _poll_quotes(self) -> None:
        subscribed = self.subscriptions.target_symbols()
        defaults = [] if subscribed else await self._discover_default_symbols()
        symbols = (subscribed or defaults)[:30]
        if not symbols:
            return
        quotes = await asyncio.to_thread(self.quote_service.get_quotes, symbols)
        errors = 0
        failed: list[str] = []
        for quote in quotes:
            status = quote.get("status")
            if status != "ok":
                errors += 1
                failed.append(f"{quote['symbol']}={status}")
            self.cache.set(quote)
            if self.subscriptions.has_subscribers(quote["symbol"]):
                self.subscriptions.publish(
                    quote["symbol"], {"type": "quote", "data": quote}
                )
        if errors:
            self._last_error = (
                f"{errors}/{len(quotes)} quote(s) unavailable: {', '.join(failed[:5])}"
            )
        else:
            self._last_error = None
            self.manager.mark_success()
        if self._poll_count % 120 == 1:
            # SQLite writes must not block the event loop.
            await asyncio.to_thread(
                record_collector_stat,
                self.session_factory,
                len(symbols),
                self._last_latency_ms,
                errors,
            )

    async def _poll_candles(self) -> None:
        if not getattr(self.settings, "CANDLE_STREAM_ENABLED", True):
            return
        pairs = self.subscriptions.candle_targets()
        active = set(pairs)
        for key in list(self._last_candle_epoch):
            if key not in active:
                self._last_candle_epoch.pop(key, None)
                self._last_candle_bucket.pop(key, None)
                self.candle_cache.remove(*key)
        now = time.time()
        for symbol, timeframe in pairs:
            key = (symbol, timeframe)
            seconds = TIMEFRAME_SECONDS.get(timeframe)
            if not seconds:
                continue
            bucket = int(now // seconds)
            if self._last_candle_bucket.get(key) == bucket:
                continue
            try:
                result = await asyncio.to_thread(
                    self.candle_service.get_candles, symbol, timeframe, 2
                )
                completed = [item for item in result["candles"] if item.get("completed")]
                if not completed:
                    continue
                candle = max(completed, key=lambda item: item.get("epoch", 0))
                # Wall-clock buckets can repeat across polls and can drift from
                # broker time; the candle epoch is the only reliable dedupe key.
                epoch = int(candle.get("epoch") or 0)
                last_epoch = self._last_candle_epoch.get(key)
                if last_epoch is not None and epoch <= last_epoch:
                    continue
                self._last_candle_epoch[key] = epoch
                self._last_candle_bucket[key] = bucket
                self.candle_cache.set(symbol, timeframe, candle)
                self._candle_events += 1
                self.subscriptions.publish_candle(
                    symbol,
                    timeframe,
                    {
                        "type": "candle",
                        "data": {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "candle": candle,
                        },
                    },
                )
            except Exception as exc:
                logger.debug("Candle stream poll failed for %s/%s: %s", symbol, timeframe, exc)

    async def _run(self) -> None:
        logger.info("Market collector started (interval=%ss)", self.settings.MARKET_POLL_INTERVAL)
        while True:
            started = time.monotonic()
            try:
                await self._poll_quotes()
                self._poll_count += 1
                self._last_poll_at = timestamp_to_iso(time.time())
                await self._poll_candles()
                if self._poll_count % 60 == 0:
                    for client_id in self.subscriptions.cleanup_stale():
                        logger.info("Removed stale WebSocket client %s", client_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("Collector poll error: %s", exc)
            self._last_latency_ms = (time.monotonic() - started) * 1000
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.01, self.settings.MARKET_POLL_INTERVAL - elapsed))

    async def on_connection_event(self, connected: bool, message: str) -> None:
        # SQLite writes must not block the event loop.
        await asyncio.to_thread(
            record_event,
            self.session_factory,
            "connection",
            message,
            level="INFO" if connected else "WARNING",
            details={"connected": connected},
        )
        self.subscriptions.publish_all(
            {
                "type": "connection",
                "data": {
                    "connected": connected,
                    "message": message,
                    "timestamp": timestamp_to_iso(time.time()),
                },
            }
        )
