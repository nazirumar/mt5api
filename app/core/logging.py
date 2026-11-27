"""Structured JSON logging with optional request correlation."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from .context import get_request_id

_CONFIGURING = False
_CONFIGURED = False
_CONFIGURED_LOG_DIR: Path | None = None
_CONFIGURED_LEVEL = ""


class StructuredFormatter(logging.Formatter):
    """Emit one JSON object per log line, safe for local log scrapers."""

    extra_fields = ("request_id", "method", "path", "status_code", "duration_ms", "client_id")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id
        for field in self.extra_fields:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> logging.Logger:
    """Attach structured handlers, switching target when settings change.

    Production configures logging once, but tests create apps with temporary
    log directories. Rebinding here keeps test runs out of the live log file.
    """
    global _CONFIGURED, _CONFIGURING, _CONFIGURED_LOG_DIR, _CONFIGURED_LEVEL
    logger = logging.getLogger("nazbeen_mt5")
    log_dir = settings.log_directory
    if _CONFIGURING:
        return logger
    if (
        _CONFIGURED
        and _CONFIGURED_LOG_DIR == log_dir
        and _CONFIGURED_LEVEL == settings.LOG_LEVEL
    ):
        return logger

    _CONFIGURING = True
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
        logger.propagate = False

        formatter = StructuredFormatter()
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        logger.addHandler(console)

        file_handler = logging.FileHandler(
            log_dir / "api.log", encoding="utf-8", delay=True
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        _CONFIGURED = True
        _CONFIGURED_LOG_DIR = log_dir
        _CONFIGURED_LEVEL = settings.LOG_LEVEL
    finally:
        _CONFIGURING = False
    return logger


def get_logger(name: str = "nazbeen_mt5") -> logging.Logger:
    return logging.getLogger(name)
