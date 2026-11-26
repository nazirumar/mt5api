"""One shared quote polling loop for REST cache and all WebSocket clients."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core.helpers import timestamp_to_iso
from ..core.logging import get_logger
from ..database.repositories import record_collector_stat, record_event
from ..mt5.connection import MT5ConnectionManager
from ..mt5.quotes import QuoteService
from ..mt5.symbols import SymbolService
from .cache import QuoteCache
from .subscription_manager import SubscriptionManager

logger = get_logger("nazbeen_mt5.services.collector")


class MarketCollector:
    def __init__(
        self,
        settings: Any,
        manager: MT5ConnectionManager,
        quote_service: QuoteService,
        symbol_service: SymbolService,
        cache: QuoteCache,
        subscriptions: SubscriptionManager,
        session_factory: Any,
    ):
        self.settings = settings
        self.manager = manager
        self.quote_service = quote_service
        self.symbol_service = symbol_service
        self.cache = cache
        self.subscriptions = subscriptions
        self.session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._default_symbols: list[str] = []
        self._poll_count = 0
        self._last_poll_at: str | None = None
        self._last_error: str | None = None
        self._last_latency_ms = 0.0

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

    async def _run(self) -> None:
        logger.info("Market collector started (interval=%ss)", self.settings.MARKET_POLL_INTERVAL)
        while True:
            started = time.monotonic()
            try:
                subscribed = self.subscriptions.target_symbols()
                defaults = [] if subscribed else await self._discover_default_symbols()
                symbols = (subscribed or defaults)[:30]
                if symbols:
                    quotes = await asyncio.to_thread(self.quote_service.get_quotes, symbols)
                    errors = 0
                    for quote in quotes:
                        if quote.get("status") != "ok":
                            errors += 1
                        self.cache.set(quote)
                        if self.subscriptions.has_subscribers(quote["symbol"]):
                            self.subscriptions.publish(
                                quote["symbol"], {"type": "quote", "data": quote}
                            )
                    if not errors:
                        self.manager.mark_success()
                    self._poll_count += 1
                    self._last_poll_at = timestamp_to_iso(time.time())
                    self._last_error = None
                    if self._poll_count % 120 == 1:
                        record_collector_stat(
                            self.session_factory, len(symbols), self._last_latency_ms, errors
                        )
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
        record_event(
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
