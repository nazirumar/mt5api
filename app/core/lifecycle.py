"""Application lifecycle helpers (startup timestamps and event bookkeeping)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RuntimeState:
    started_at: float = field(default_factory=time.monotonic)
    start_wall_clock: str = field(default_factory=iso_utc)
    startup_errors: list[str] = field(default_factory=list)

    @property
    def uptime_seconds(self) -> float:
        return round(time.monotonic() - self.started_at, 3)
