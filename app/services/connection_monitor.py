"""Background MT5 health monitor with automatic reconnection."""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.logging import get_logger
from ..mt5.connection import MT5ConnectionManager
from .market_collector import MarketCollector

logger = get_logger("nazbeen_mt5.services.monitor")


class ConnectionMonitor:
    def __init__(self, settings: Any, manager: MT5ConnectionManager, collector: MarketCollector):
        self.settings = settings
        self.manager = manager
        self.collector = collector
        self._task: asyncio.Task | None = None
        self._was_connected: bool | None = None
        self._reconnecting = False

    def stats(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "last_known_connected": self._was_connected,
            "reconnecting": self._reconnecting,
        }

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="mt5-connection-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("Connection monitor started")
        interval = max(self.settings.MT5_RECONNECT_INTERVAL, 1.0)
        while True:
            try:
                connected = bool(await asyncio.to_thread(self.manager.is_connected))
                if connected != self._was_connected:
                    message = "MT5 terminal connected" if connected else "MT5 terminal disconnected"
                    await self.collector.on_connection_event(connected, message)
                    self._was_connected = connected
                if not connected and not self._reconnecting:
                    self._reconnecting = True
                    try:
                        await self.manager.reconnect_async()
                    finally:
                        self._reconnecting = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Monitor iteration error: %s", exc)
            await asyncio.sleep(interval)
