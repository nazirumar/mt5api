#!/usr/bin/env python3
"""Standalone asyncio WebSocket client for the Nazbeen MT5 Market Data API.

Install:
    python -m pip install websockets

Environment:
    API_BASE_URL     Default: http://mt5.nazbeen.test
    API_KEY      Required (MT5_API_KEY accepted as a compatibility fallback)
    MT5_WS_SYMBOLS   Optional, default: XAUUSD,EURUSD
    MT5_WS_CANDLES   Optional, comma-separated SYMBOL:TIMEFRAME pairs, e.g.
                     XAUUSD:M1,EURUSD:M5

Read-only: subscribes to quotes only. It never sends trading commands.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from typing import Any

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover
    print("Missing dependency. Install with: python -m pip install websockets", file=sys.stderr)
    websockets = None  # type: ignore[assignment]

    class ConnectionClosed(Exception):  # type: ignore[no-redef]
        pass


DEFAULT_BASE_URL = "http://mt5.nazbeen.test"
DEFAULT_SYMBOLS = ("XAUUSD", "EURUSD")
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
PING_AFTER_IDLE_SECONDS = 30.0
PING_REPEAT_SECONDS = 10.0
MAX_MISSED_PONGS = 3


class ConfigurationError(Exception):
    """Raised when required local configuration is missing."""


def build_ws_url(base_url: str) -> str:
    """Convert an HTTP base URL to the market WebSocket URL.

    Authentication is sent as the first frame, so the API key stays out of the
    URL and proxy logs. Query-string auth remains supported for older clients.
    """
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    elif base.startswith("ws://") or base.startswith("wss://"):
        ws_base = base
    else:
        ws_base = "ws://" + base
    return f"{ws_base}/ws/market"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class MarketWebSocketClient:
    """Auto-reconnecting, read-only quote stream client."""

    def __init__(
        self,
        url: str,
        symbols: list[str],
        api_key: str,
        candles: list[dict[str, str]] | None = None,
    ) -> None:
        self.url = url
        self.symbols = symbols
        self.api_key = api_key
        self.candles = candles or []
        self.stop_event = asyncio.Event()
        self.last_message_at = time.monotonic()
        self.authenticated_sent = False
        self.subscribed_sent = False
        self.candles_sent = False
        self._ws: Any = None

    def request_stop(self) -> None:
        """Request graceful shutdown from a signal or keyboard interrupt."""
        self.stop_event.set()
        ws = self._ws
        if ws is not None:
            asyncio.create_task(self._close_socket(ws))

    @staticmethod
    async def _close_socket(ws: Any) -> None:
        try:
            await ws.close()
        except Exception:
            pass

    async def _send_json(self, ws: Any, payload: dict[str, Any]) -> None:
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def run(self) -> None:
        backoff = INITIAL_BACKOFF_SECONDS

        while not self.stop_event.is_set():
            if websockets is None:
                print("Cannot connect: websockets library is not installed.", file=sys.stderr)
                return

            print(f"Connecting to {self.url.split('?')[0]} ...")
            heartbeat_task: asyncio.Task[Any] | None = None

            try:
                async with websockets.connect(self.url, ping_interval=None) as ws:
                    self._ws = ws
                    self.last_message_at = time.monotonic()
                    self.authenticated_sent = False
                    self.subscribed_sent = False
                    self.candles_sent = False
                    backoff = INITIAL_BACKOFF_SECONDS
                    print("Connected.")

                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                    await self._consume(ws)

            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt:
                self.stop_event.set()
            except ConnectionClosed as exc:
                print(f"Connection closed: {exc}")
            except Exception as exc:
                print(f"Connection error: {exc}")
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except (asyncio.CancelledError, Exception):
                        pass

                if self._ws is not None:
                    await self._safe_unsubscribe(self._ws)
                    self._ws = None

            if self.stop_event.is_set():
                break

            print(f"Reconnecting in {backoff:.1f}s ...")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2.0, MAX_BACKOFF_SECONDS)

    async def _consume(self, ws: Any) -> None:
        async for raw in ws:
            if self.stop_event.is_set():
                break
            self.last_message_at = time.monotonic()
            await self._handle_message(raw, ws)

    async def _handle_message(self, raw: Any, ws: Any) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            print(f"Non-JSON message: {raw}")
            return

        if not isinstance(message, dict):
            print(f"Unexpected message shape: {raw}")
            return

        msg_type = str(message.get("type", "unknown"))
        data = message.get("data")

        if msg_type == "auth_required":
            if not self.authenticated_sent:
                await self._send_json(ws, {"action": "auth", "api_key": self.api_key})
                self.authenticated_sent = True
                print("Authenticating in the first WebSocket frame.")

        elif msg_type == "authenticated":
            print("WebSocket authenticated.")

        elif msg_type == "welcome":
            print(f"Welcome: {compact_json(data if data is not None else message)}")
            if not self.subscribed_sent:
                await self._send_json(
                    ws,
                    {"action": "subscribe", "symbols": self.symbols},
                )
                self.subscribed_sent = True
                print(f"Subscribed request sent for: {', '.join(self.symbols)}")
            if self.candles and not self.candles_sent:
                await self._send_json(
                    ws,
                    {"action": "subscribe_candles", "candles": self.candles},
                )
                self.candles_sent = True
                print("Completed-candle stream subscribed.")

        elif msg_type == "subscribed":
            print(f"Subscribed: {compact_json(data if data is not None else message)}")

        elif msg_type == "subscribed_candles":
            print(f"Candle stream: {compact_json(data if data is not None else message)}")

        elif msg_type == "candle":
            print(f"Candle closed: {compact_json(data if data is not None else message)}")

        elif msg_type == "unsubscribed":
            print(f"Unsubscribed: {compact_json(data if data is not None else message)}")

        elif msg_type == "quote":
            # Quotes are inside `data`.
            print(f"Quote: {compact_json(data if data is not None else message)}")

        elif msg_type == "connection":
            print(f"Connection event: {compact_json(data if data is not None else message)}")

        elif msg_type == "heartbeat":
            print("Heartbeat received.")

        elif msg_type == "pong":
            print("Pong received.")

        elif msg_type == "error":
            error = message.get("error", message.get("message", message))
            print(f"Server error: {compact_json(error)}", file=sys.stderr)

        else:
            print(f"{msg_type}: {compact_json(message)}")

    async def _heartbeat_loop(self, ws: Any) -> None:
        """Send lightweight ping frames if the server goes quiet."""
        missed = 0
        last_ping = 0.0

        while not self.stop_event.is_set():
            await asyncio.sleep(1.0)
            now = time.monotonic()
            idle = now - self.last_message_at

            if idle < PING_AFTER_IDLE_SECONDS:
                missed = 0
                continue

            if now - last_ping < PING_REPEAT_SECONDS:
                continue

            try:
                await self._send_json(ws, {"action": "ping"})
                last_ping = now
                missed += 1
                print("No server messages; sent ping.")
            except Exception as exc:
                print(f"Ping failed: {exc}")
                await self._close_socket(ws)
                return

            if missed >= MAX_MISSED_PONGS:
                print("No heartbeat/pong received; reconnecting.")
                await self._close_socket(ws)
                return

    async def _safe_unsubscribe(self, ws: Any) -> None:
        if not self.subscribed_sent:
            return
        try:
            await asyncio.wait_for(
                self._send_json(
                    ws,
                    {"action": "unsubscribe", "symbols": self.symbols},
                ),
                timeout=2.0,
            )
            print("Unsubscribe request sent.")
        except Exception as exc:
            print(f"Unsubscribe skipped: {exc}")
        if self.candles_sent:
            try:
                await asyncio.wait_for(
                    self._send_json(
                        ws,
                        {"action": "unsubscribe_candles", "candles": self.candles},
                    ),
                    timeout=2.0,
                )
                print("Candle unsubscribe request sent.")
            except Exception as exc:
                print(f"Candle unsubscribe skipped: {exc}")


async def main() -> int:
    api_key = os.getenv("API_KEY") or os.getenv("MT5_API_KEY")
    if not api_key:
        print(
            "API_KEY is not set. For example:\n"
            "  $env:API_KEY='your-long-random-secret'\n"
            "MT5_API_KEY is accepted as a compatibility fallback.",
            file=sys.stderr,
        )
        return 2

    base_url = os.getenv("API_BASE_URL") or DEFAULT_BASE_URL
    raw_symbols = os.getenv("MT5_WS_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]
    if not symbols:
        print("MT5_WS_SYMBOLS is empty.", file=sys.stderr)
        return 2

    raw_candles = os.getenv("MT5_WS_CANDLES", "")
    candles = []
    for item in raw_candles.split(","):
        parts = item.strip().split(":")
        if len(parts) != 2 or not all(parts):
            continue
        candles.append({"symbol": parts[0], "timeframe": parts[1]})

    client = MarketWebSocketClient(build_ws_url(base_url), symbols, api_key, candles)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, client.request_stop)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows / restricted environments may not support all signals.
            pass

    print("Press Ctrl+C to stop.")
    try:
        await client.run()
    except KeyboardInterrupt:
        client.request_stop()
        await client.run()
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
