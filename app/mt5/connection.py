"""Single-owner, thread-safe MetaTrader 5 connection manager.

The MetaTrader5 Python package is synchronous and not safe for concurrent
calls from multiple threads.  Every MT5 operation in this application goes
through :meth:`MT5ConnectionManager.call`, which serializes access with a
re-entrant lock.  Async callers use ``asyncio.to_thread`` so the FastAPI event
loop is never blocked by a synchronous MT5 call.
"""

from __future__ import annotations

import asyncio
import importlib
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..core.exceptions import MT5NotInitializedError, MT5OperationError
from ..core.logging import get_logger
from .terminal import discover_terminal_path, is_terminal_process_running, launch_terminal

logger = get_logger("nazbeen_mt5.mt5.connection")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MT5ConnectionManager:
    """Owns the MT5 lifecycle and serializes all MT5 package calls."""

    def __init__(self, settings: Settings, mt5_module: Any | None = None):
        self.settings = settings
        self._lock = threading.RLock()
        self._module = mt5_module
        self._initialized = False
        self._terminal_path: Path | None = None
        self._broker: str | None = None
        self._server: str | None = None
        self._account_login: int | None = None
        self._terminal_connected = False
        self._last_error: str | None = None
        self._last_success: str | None = None
        self._reconnect_attempts = 0
        self._initialized_at: str | None = None

    # ------------------------------------------------------------------ #
    # module / safety helpers
    # ------------------------------------------------------------------ #
    def module(self) -> Any:
        if self._module is None:
            self._module = importlib.import_module("MetaTrader5")
        return self._module

    def _sanitize(self, message: str) -> str:
        if self.settings.MT5_PASSWORD:
            message = message.replace(self.settings.MT5_PASSWORD, "***")
        return message

    def _last_mt5_error(self) -> str | None:
        try:
            error = self.module().last_error()
            if not error:
                return None
            if isinstance(error, tuple) and len(error) >= 2:
                return self._sanitize(f"{error[0]}: {error[1]}")
            return self._sanitize(str(error))
        except Exception:  # pragma: no cover - defensive
            return None

    def inspect(self) -> dict[str, Any]:
        """Read-only connection snapshot. Safe to call from any thread."""
        with self._lock:
            return {
                "initialized": self._initialized,
                "initialized_at": self._initialized_at,
                "terminal_connected": self._terminal_connected,
                "terminal_path": str(self._terminal_path) if self._terminal_path else None,
                "broker": self._broker,
                "server": self._server,
                "account_login": self._account_login,
                "last_success": self._last_success,
                "last_error": self._last_error,
                "reconnect_attempts": self._reconnect_attempts,
            }

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def ensure_terminal_running(self) -> Path | None:
        path = self._terminal_path or discover_terminal_path(self.settings)
        if path:
            self._terminal_path = path
            if not is_terminal_process_running(path) and self.settings.MT5_START_TERMINAL:
                logger.info("Launching MetaTrader 5 terminal: %s", path)
                launch_terminal(path)
        elif self.settings.MT5_START_TERMINAL:
            logger.warning(
                "MetaTrader 5 terminal executable was not found. Set MT5_PATH in .env "
                "or start the terminal manually."
            )
        return path

    def initialize(self) -> bool:
        """Initialize MT5. Credentials are optional when terminal is logged in."""
        with self._lock:
            mod = self.module()
            path = self.ensure_terminal_running()
            kwargs: dict[str, Any] = {
                "path": str(path) if path else "",
                "timeout": self.settings.MT5_INIT_TIMEOUT,
            }
            if self.settings.MT5_LOGIN is not None:
                kwargs.update(
                    login=self.settings.MT5_LOGIN,
                    password=self.settings.MT5_PASSWORD,
                    server=self.settings.MT5_SERVER,
                )
            try:
                ok = bool(mod.initialize(**kwargs))
            except Exception as exc:
                ok = False
                self._last_error = self._sanitize(str(exc))

            if not ok:
                self._initialized = False
                self._terminal_connected = False
                self._last_error = self._last_error or self._last_mt5_error() or "MT5 initialization failed"
                logger.warning("MT5 initialization failed: %s", self._last_error)
                return False

            self._initialized = True
            self._last_error = None
            self._initialized_at = _utc_now()
            self._refresh_metadata(mod)
            logger.info(
                "MT5 initialized (broker=%s, server=%s, connected=%s)",
                self._broker or "unknown",
                self._server or "unknown",
                self._terminal_connected,
            )
            return True

    def _refresh_metadata(self, mod: Any) -> None:
        try:
            terminal_info = mod.terminal_info()
            if terminal_info is not None:
                self._terminal_connected = bool(getattr(terminal_info, "connected", False))
                self._broker = getattr(terminal_info, "company", None) or self._broker
                path_value = getattr(terminal_info, "path", None)
                if path_value:
                    self._terminal_path = Path(path_value)
        except Exception as exc:
            self._terminal_connected = False
            logger.debug("Could not read terminal info: %s", self._sanitize(str(exc)))

        try:
            account = mod.account_info()
            if account is not None:
                self._server = getattr(account, "server", None) or self._server
                self._account_login = getattr(account, "login", None)
        except Exception as exc:
            logger.debug("Could not read account info: %s", self._sanitize(str(exc)))

    def shutdown(self) -> None:
        with self._lock:
            if not self._initialized:
                return
            try:
                self.module().shutdown()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("MT5 shutdown error: %s", self._sanitize(str(exc)))
            self._initialized = False
            self._terminal_connected = False
            logger.info("MT5 connection closed")

    async def initialize_async(self) -> bool:
        return await asyncio.to_thread(self.initialize)

    async def reconnect_async(self) -> bool:
        """Bounded exponential-backoff reconnect used by the monitor."""
        delay = self.settings.MT5_RECONNECT_INTERVAL
        for attempt in range(1, self.settings.MT5_MAX_RECONNECT_ATTEMPTS + 1):
            self._reconnect_attempts = attempt
            logger.info("MT5 reconnect attempt %d/%d", attempt, self.settings.MT5_MAX_RECONNECT_ATTEMPTS)
            await asyncio.to_thread(self.shutdown)
            await asyncio.to_thread(self.ensure_terminal_running)
            if await self.initialize_async():
                info = await self.get_terminal_info_async()
                if info is not None and bool(getattr(info, "connected", False)):
                    self._reconnect_attempts = 0
                    self._last_error = None
                    self._last_success = _utc_now()
                    logger.info("MT5 reconnected successfully")
                    return True
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.settings.MT5_MAX_RECONNECT_INTERVAL)
        self._last_error = self._last_error or "MT5 reconnect attempts exhausted"
        logger.error("MT5 reconnect failed after %d attempts", self.settings.MT5_MAX_RECONNECT_ATTEMPTS)
        return False

    # ------------------------------------------------------------------ #
    # synchronized call interface
    # ------------------------------------------------------------------ #
    def call(
        self,
        name: str,
        *args: Any,
        allow_none: bool = False,
        check_result: bool = True,
        **kwargs: Any,
    ) -> Any:
        with self._lock:
            if not self._initialized:
                raise MT5NotInitializedError()
            func: Callable[..., Any] | None = getattr(self.module(), name, None)
            if func is None or not callable(func):
                raise MT5OperationError(f"MetaTrader5 package has no operation '{name}'")
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                self._last_error = self._sanitize(str(exc))
                raise MT5OperationError(f"MT5 operation '{name}' failed: {self._last_error}") from None

            if result is None and not allow_none:
                self._last_error = self._last_mt5_error() or f"MT5 operation '{name}' returned no data"
                raise MT5OperationError(self._last_error)
            if check_result and result is False:
                self._last_error = self._last_mt5_error() or f"MT5 operation '{name}' was rejected"
                raise MT5OperationError(self._last_error)
            if result is not None and result is not False:
                self._last_success = _utc_now()
            return result

    def get_terminal_info(self) -> Any:
        with self._lock:
            if not self._initialized:
                return None
            try:
                info = self.module().terminal_info()
            except Exception:
                return None
            if info is not None:
                self._terminal_connected = bool(getattr(info, "connected", False))
                self._broker = getattr(info, "company", None) or self._broker
            return info

    async def get_terminal_info_async(self) -> Any:
        return await asyncio.to_thread(self.get_terminal_info)

    def is_connected(self) -> bool:
        info = self.get_terminal_info()
        return bool(info is not None and getattr(info, "connected", False))

    def mark_success(self) -> None:
        with self._lock:
            self._last_success = _utc_now()

    def health(self) -> dict[str, Any]:
        snapshot = self.inspect()
        info = self.get_terminal_info()
        if info is not None:
            snapshot["terminal_connected"] = bool(getattr(info, "connected", False))
            snapshot["broker"] = getattr(info, "company", None) or snapshot["broker"]
        return snapshot
