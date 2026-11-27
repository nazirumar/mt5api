"""Configuration loading and validation.

All settings are read from the process environment, with ``.env`` used as a
fallback.  Credentials are never written to source code or logs.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_environment() -> None:
    """Merge ``.env`` into os.environ without overriding real env vars."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for key, value in dotenv_values(env_path).items():
        if value is not None and key not in os.environ:
            os.environ[key] = value


_load_environment()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


class Settings(BaseModel):
    """Validated application settings.

    Field names intentionally match the environment variable names from
    ``.env.example`` so configuration is unsurprising for operators.
    """

    APP_NAME: str = "Nazbeen MT5 Market Data API"
    APP_ENV: str = "production"
    APP_VERSION: str = "1.0.0"

    API_HOST: str = "127.0.0.1"
    API_PORT: int = Field(default=8080, ge=1, le=65535)
    API_KEY: str = "CHANGE_ME_TO_A_RANDOM_SECRET"
    API_KEY_PREVIOUS: str = ""
    API_KEY_HEADER: str = "X-API-Key"

    LOCAL_DOMAIN: str = "mt5.nazbeen.test"
    ENABLE_CADDY: bool = True
    ALLOW_LAN_ACCESS: bool = False
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://mt5.nazbeen.test", "http://127.0.0.1:8080"])

    MT5_PATH: str = ""
    MT5_LOGIN: int | None = None
    MT5_PASSWORD: str = ""
    MT5_SERVER: str = ""
    # MT5 timestamps encode broker server time. Set an IANA timezone or a
    # fixed UTC offset so history is exported in real UTC.
    MT5_SERVER_TIMEZONE: str = ""
    MT5_SERVER_UTC_OFFSET_MINUTES: int | None = None
    MT5_START_TERMINAL: bool = True
    MT5_INIT_TIMEOUT: int = Field(default=60, ge=1, le=600)
    MT5_RECONNECT_INTERVAL: float = Field(default=5.0, gt=0)
    MT5_MAX_RECONNECT_INTERVAL: float = Field(default=60.0, gt=0)
    MT5_MAX_RECONNECT_ATTEMPTS: int = Field(default=10, ge=1, le=1000)

    MARKET_POLL_INTERVAL: float = Field(default=0.5, gt=0)
    QUOTE_STALE_MULTIPLIER: float = Field(default=3.0, gt=0)
    WATCHED_SYMBOLS: list[str] = Field(default_factory=list)
    DEFAULT_CANDLE_LIMIT: int = Field(default=500, ge=1, le=10000)
    MAX_CANDLE_LIMIT: int = Field(default=5000, ge=1, le=100000)
    MAX_TICK_RECORDS: int = Field(default=10000, ge=1, le=200000)
    MAX_CANDLE_RANGE_DAYS: int = Field(default=365, ge=1, le=3650)
    MAX_TICK_RANGE_DAYS: int = Field(default=30, ge=1, le=365)
    HISTORY_RATE_LIMIT_PER_MINUTE: int = Field(default=30, ge=1, le=1000)
    HISTORY_MAX_CONCURRENT: int = Field(default=2, ge=1, le=16)
    CANDLE_STREAM_ENABLED: bool = True
    CANDLE_STREAM_MAX_SUBSCRIPTIONS: int = Field(default=50, ge=1, le=500)

    DATABASE_URL: str = "sqlite:///./data/market_data.db"
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"
    WEBSOCKET_HEARTBEAT_INTERVAL: float = Field(default=30.0, gt=0)
    WEBSOCKET_STALE_TIMEOUT: float = Field(default=90.0, gt=0)
    DISABLE_BACKGROUND: bool = False

    @field_validator("CORS_ORIGINS", "WATCHED_SYMBOLS", mode="before")
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        return _csv(value)

    @field_validator("MT5_LOGIN", mode="before")
    @classmethod
    def _empty_login(cls, value: Any) -> Any:
        if value in (None, "", "None"):
            return None
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def _log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if not self.ALLOW_LAN_ACCESS and self.API_HOST not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("API_HOST must be loopback unless ALLOW_LAN_ACCESS=true")
        if self.MT5_MAX_RECONNECT_INTERVAL < self.MT5_RECONNECT_INTERVAL:
            raise ValueError("MT5_MAX_RECONNECT_INTERVAL must be >= MT5_RECONNECT_INTERVAL")
        if self.DEFAULT_CANDLE_LIMIT > self.MAX_CANDLE_LIMIT:
            raise ValueError("DEFAULT_CANDLE_LIMIT must be <= MAX_CANDLE_LIMIT")
        if self.MT5_SERVER_TIMEZONE and self.MT5_SERVER_UTC_OFFSET_MINUTES is not None:
            raise ValueError(
                "Set only one of MT5_SERVER_TIMEZONE or MT5_SERVER_UTC_OFFSET_MINUTES"
            )
        if not self.DATABASE_URL.startswith("sqlite:"):
            raise ValueError("Only SQLite DATABASE_URL values are supported")
        if self.APP_ENV.lower() == "production":
            if not self.API_KEY or self.API_KEY.startswith("CHANGE_ME"):
                raise ValueError("API_KEY must be set to a random secret in production")
            if len(self.API_KEY) < 16:
                raise ValueError("API_KEY must be at least 16 characters in production")
            if self.API_KEY_PREVIOUS.startswith("CHANGE_ME"):
                raise ValueError("API_KEY_PREVIOUS must be empty or a real secret")
        return self

    @property
    def api_keys(self) -> tuple[str, ...]:
        """Current and previous keys; used for zero-downtime rotation."""
        keys = [self.API_KEY]
        if self.API_KEY_PREVIOUS and self.API_KEY_PREVIOUS != self.API_KEY:
            keys.append(self.API_KEY_PREVIOUS)
        return tuple(key for key in keys if key)

    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.DATABASE_URL.startswith(prefix):
            return PROJECT_ROOT / "data" / "market_data.db"
        raw = self.DATABASE_URL[len(prefix) :]
        path = Path(raw)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def log_directory(self) -> Path:
        path = Path(self.LOG_DIR)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def public_summary(self) -> dict[str, Any]:
        """Safe settings snapshot (never includes credentials or API keys)."""
        return {
            "app_name": self.APP_NAME,
            "app_env": self.APP_ENV,
            "app_version": self.APP_VERSION,
            "api_host": self.API_HOST,
            "api_port": self.API_PORT,
            "local_domain": self.LOCAL_DOMAIN,
            "enable_caddy": self.ENABLE_CADDY,
            "market_poll_interval": self.MARKET_POLL_INTERVAL,
            "watched_symbols": self.WATCHED_SYMBOLS,
            "mt5_server_timezone": self.MT5_SERVER_TIMEZONE or None,
            "mt5_server_utc_offset_minutes": self.MT5_SERVER_UTC_OFFSET_MINUTES,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(**dict(os.environ))
