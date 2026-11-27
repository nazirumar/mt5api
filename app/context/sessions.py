"""Shared, deterministic market-session context.

The original ``/market/sessions`` endpoint used approximate UTC windows. Those
windows remain the defaults here so existing behavior is unchanged. The context
engine can additionally use an IANA session timezone (DST-aware) and optional
operator-supplied windows. Broker trading hours are not exposed reliably enough
by the current MT5 adapter, so classifications remain UNKNOWN/UNVERIFIED.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WINDOWS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "Sydney": ((21, 0), (6, 0)),
    "Tokyo": ((0, 0), (9, 0)),
    "London": ((7, 0), (16, 0)),
    "New York": ((12, 0), (21, 0)),
}


def in_window(hour: int, start: int, end: int) -> bool:
    if start < end:
        return start <= hour < end
    # Overnight sessions (e.g. Sydney 21:00-06:00) wrap past midnight.
    return hour >= start or hour < end


# Backwards-compatible name used by app/api/market.py and its regression tests.
_in_window = in_window


def _windows(params: Mapping[str, Any] | None) -> tuple[dict[str, tuple[tuple[int, int], tuple[int, int]]], str | None]:
    configured = (params or {}).get("CONTEXT_SESSION_WINDOWS") or ""
    if not configured:
        return dict(WINDOWS), None
    try:
        if isinstance(configured, Mapping):
            pairs = dict(configured)
        else:
            pairs = {}
            for part in str(configured).split(","):
                name, _, span = part.strip().partition("=")
                start_text, _, end_text = span.strip().partition("-")
                pairs[name.strip()] = (start_text.strip(), end_text.strip())
        result: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {}
        for name, span in pairs.items():
            start_raw, end_raw = span
            start = (int(str(start_raw).split(":")[0]), int(str(start_raw).split(":")[1]) if ":" in str(start_raw) else 0)
            end = (int(str(end_raw).split(":")[0]), int(str(end_raw).split(":")[1]) if ":" in str(end_raw) else 0)
            if not name or not (0 <= start[0] <= 23 and 0 <= end[0] <= 23):
                raise ValueError("invalid session hour")
            result[name] = (start, end)
        if not result:
            raise ValueError("no sessions configured")
        return result, None
    except (AttributeError, TypeError, ValueError) as exc:
        return dict(WINDOWS), f"Invalid CONTEXT_SESSION_WINDOWS value; using defaults: {type(exc).__name__}"


def _active_sessions(
    windows: Mapping[str, tuple[tuple[int, int], tuple[int, int]]], hour: int
) -> list[str]:
    return [
        name for name, (start, end) in windows.items()
        if in_window(hour, start[0], end[0])
    ]


def session_context(
    now: datetime | None = None,
    broker_time: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build session context; never claims broker-verified trading hours."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_utc = current.astimezone(timezone.utc)
    windows, warning = _windows(params)
    timezone_name = str((params or {}).get("CONTEXT_SESSION_TIMEZONE") or "UTC")
    try:
        session_tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        session_tz = timezone.utc
        warning = f"Invalid CONTEXT_SESSION_TIMEZONE '{timezone_name}'; using UTC"

    local_now = current_utc.astimezone(session_tz)
    active = _active_sessions(windows, local_now.hour)
    states = {
        name: ("OPEN" if name in active else "CLOSED")
        for name in ("London", "New York", "Tokyo", "Sydney")
    }
    overlap = bool("London" in active and "New York" in active)

    base = current_utc.replace(minute=0, second=0, microsecond=0)
    transition: dict[str, Any] | None = None
    for offset in range(1, 25):
        candidate = base + timedelta(hours=offset)
        candidate_hour = candidate.astimezone(session_tz).hour
        candidate_active = _active_sessions(windows, candidate_hour)
        if candidate_active != active:
            transition = {
                "at": candidate.isoformat().replace("+00:00", "Z"),
                "in_hours": offset,
                "active_sessions_after": candidate_active,
            }
            break

    return {
        "classification": "UNKNOWN",
        "state": "ACTIVE" if active else "INACTIVE",
        "verified": False,
        "timezone": timezone_name,
        "current_utc": current_utc.isoformat().replace("+00:00", "Z"),
        "broker_time": broker_time,
        "local_session_time": local_now.isoformat(),
        "active_sessions": active,
        "upcoming_transition": transition,
        "session_states": {
            "london": states["London"],
            "new_york": states["New York"],
            "asian": states["Tokyo"],
            "sydney": states["Sydney"],
        },
        "overlap": {
            "london_new_york": overlap,
            "active": overlap,
        },
        "note": (
            "Session windows are approximate and not broker-verified. Broker time "
            "is reported separately and is not assumed to equal UTC."
        ),
        "warning": warning,
        "method": "Configured local-hour windows with end-exclusive overnight handling and DST-aware timezone conversion",
    }
