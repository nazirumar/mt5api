"""Structured, low-overhead file and console logging."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..config import Settings

_CONFIGURED = False


def configure_logging(settings: Settings) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("nazbeen_mt5")
    if _CONFIGURED:
        return logger

    settings.log_directory.mkdir(parents=True, exist_ok=True)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(
        settings.log_directory / "api.log", encoding="utf-8", delay=True
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _CONFIGURED = True
    return logger


def get_logger(name: str = "nazbeen_mt5") -> logging.Logger:
    return logging.getLogger(name)
