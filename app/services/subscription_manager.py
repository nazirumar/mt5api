"""WebSocket subscription registry with bounded per-client queues."""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Client:
    id: int
    symbols: set[str] = field(default_factory=set)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))
    connected_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)


class SubscriptionManager:
    """One shared market feed fans out to every subscribed client."""

    def __init__(self, max_queue: int = 64, stale_timeout: float = 90.0):
        self._clients: dict[int, Client] = {}
        self._counter = itertools.count(1)
        self._max_queue = max_queue
        self._stale_timeout = stale_timeout

    def connect(self) -> int:
        client_id = next(self._counter)
        self._clients[client_id] = Client(id=client_id, queue=asyncio.Queue(maxsize=self._max_queue))
        return client_id

    def touch(self, client_id: int) -> None:
        client = self._clients.get(client_id)
        if client:
            client.last_seen = time.monotonic()

    def subscribe(self, client_id: int, symbols: list[str]) -> set[str]:
        client = self._clients.get(client_id)
        if not client:
            return set()
        client.symbols.update(symbols)
        client.last_seen = time.monotonic()
        return set(client.symbols)

    def unsubscribe(self, client_id: int, symbols: list[str]) -> set[str]:
        client = self._clients.get(client_id)
        if not client:
            return set()
        client.symbols.difference_update(symbols)
        client.last_seen = time.monotonic()
        return set(client.symbols)

    def disconnect(self, client_id: int) -> None:
        self._clients.pop(client_id, None)

    def queue_for(self, client_id: int) -> asyncio.Queue | None:
        client = self._clients.get(client_id)
        return client.queue if client else None

    def is_active(self, client_id: int) -> bool:
        return client_id in self._clients

    def is_stale(self, client_id: int) -> bool:
        client = self._clients.get(client_id)
        return bool(client and time.monotonic() - client.last_seen > self._stale_timeout)

    def has_subscribers(self, symbol: str) -> bool:
        return any(symbol in client.symbols for client in self._clients.values())

    def target_symbols(self) -> list[str]:
        symbols: set[str] = set()
        for client in self._clients.values():
            symbols.update(client.symbols)
        return sorted(symbols)

    def active_count(self) -> int:
        return len(self._clients)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "client_id": client.id,
                "symbols": sorted(client.symbols),
                "connected_seconds": round(time.monotonic() - client.connected_at, 1),
                "idle_seconds": round(time.monotonic() - client.last_seen, 1),
            }
            for client in self._clients.values()
        ]

    def publish(self, symbol: str, message: dict[str, Any]) -> None:
        for client in list(self._clients.values()):
            if symbol in client.symbols:
                self._enqueue(client, message)

    def publish_all(self, message: dict[str, Any]) -> None:
        for client in list(self._clients.values()):
            self._enqueue(client, message)

    def _enqueue(self, client: Client, message: dict[str, Any]) -> None:
        try:
            client.queue.put_nowait(message)
        except asyncio.QueueFull:
            try:
                client.queue.get_nowait()  # drop oldest quote, keep stream fresh
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    def cleanup_stale(self) -> list[int]:
        now = time.monotonic()
        stale = [
            client.id for client in self._clients.values()
            if now - client.last_seen > self._stale_timeout
        ]
        for client_id in stale:
            self.disconnect(client_id)
        return stale
