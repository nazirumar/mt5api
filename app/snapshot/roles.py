"""Informational timeframe role labels.

These labels are descriptive conventions only. They never force a trading
decision and clients are free to interpret timeframes differently.
"""

from __future__ import annotations

TIMEFRAME_ROLES: dict[str, str] = {
    "D1": "daily macro context",
    "H4": "major structure",
    "H1": "trend context",
    "M30": "intermediate structure",
    "M15": "setup context",
    "M5": "execution context",
    "M1": "detailed execution context",
}


def timeframe_role(timeframe: str) -> str | None:
    """Return the informational role for a supported timeframe, if known."""
    return TIMEFRAME_ROLES.get(str(timeframe).strip().upper())
