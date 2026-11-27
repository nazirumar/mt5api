"""MT5 access-layer tests using the fake terminal (never live data)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import MT5NotInitializedError
from app.mt5.connection import MT5ConnectionManager


@pytest.fixture
def manager(settings, fake_mt5):
    """A standalone manager; its module() resolves to the patched fake MT5."""
    return MT5ConnectionManager(settings)


def test_manager_requires_initialization(manager):
    with pytest.raises(MT5NotInitializedError):
        manager.call("symbols_get")


def test_initialize_and_safe_call(manager, fake_mt5):
    assert manager.initialize() is True
    assert manager.is_connected() is True
    symbols = manager.call("symbols_get")
    assert len(symbols) == 3
    assert manager.health()["broker"] == "Fake Broker"
    manager.shutdown()
    assert manager.health()["initialized"] is False
    assert fake_mt5.shutdown_count >= 1


def test_reconnect_keeps_executable_path(manager, settings, fake_mt5, tmp_path):
    executable = tmp_path / "terminal64.exe"
    executable.write_bytes(b"")
    object.__setattr__(settings, "MT5_PATH", str(executable))
    fake_mt5.terminal_info = lambda: SimpleNamespace(
        connected=True, company="Fake Broker", path=str(tmp_path)
    )

    assert manager.initialize() is True
    assert manager._terminal_path == executable
    assert manager._terminal_path.is_file()
    manager.shutdown()


def test_credentials_never_appear_in_health(manager, settings):
    object.__setattr__(settings, "MT5_PASSWORD", "SuperSecret123")
    manager.initialize()
    assert "SuperSecret123" not in str(manager.health())
    assert "SuperSecret123" not in str(manager.inspect())


@pytest.mark.asyncio
async def test_reconnect_with_bounded_backoff(manager, fake_mt5, settings):
    object.__setattr__(settings, "MT5_RECONNECT_INTERVAL", 0.01)
    object.__setattr__(settings, "MT5_MAX_RECONNECT_INTERVAL", 0.02)
    object.__setattr__(settings, "MT5_MAX_RECONNECT_ATTEMPTS", 3)
    fake_mt5.initialize_failures = 2
    assert await manager.reconnect_async() is True
    assert manager.health()["reconnect_attempts"] == 0


def test_terminal_disconnection_is_detected(manager, fake_mt5):
    manager.initialize()
    original = fake_mt5.terminal_info
    fake_mt5.terminal_info = lambda: __import__("types").SimpleNamespace(connected=False)
    try:
        assert manager.is_connected() is False
    finally:
        fake_mt5.terminal_info = original
