"""Broker-time to UTC conversion for MT5 quotes and history.

MetaTrader5 timestamps are Unix-style numbers, but they encode the broker
terminal's server time rather than UTC. Without an explicit conversion, a
UTC+2 broker produces timestamps two hours in the future and every candle
looks like it is still forming.

Configure either ``MT5_SERVER_TIMEZONE`` (an IANA name such as
``Etc/GMT-2`` or ``Europe/London``) or ``MT5_SERVER_UTC_OFFSET_MINUTES``
(a fixed offset such as ``120``). If neither is set, the clock makes a best
effort estimate from a fresh tick, rounded to 15 minutes.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.helpers import attr, timestamp_to_iso
from ..core.logging import get_logger

logger = get_logger("nazbeen_mt5.mt5.clock")


class BrokerClock:
    """Converts broker-native timestamps to real UTC timestamps."""

    # Auto-detection must only learn from fresh live ticks. Historical samples
    # are valid market data but useless as a clock reference.
    MAX_OBSERVATION_AGE_SECONDS = 60.0
    MAX_FUTURE_SKEW_SECONDS = 86400.0

    def __init__(self, settings: Any):
        self.settings = settings
        self._zone: ZoneInfo | None = None
        self._offset_seconds: int | None = None
        self._observed_at = 0.0
        self._invalid_timezone: str | None = None

        timezone_name = str(getattr(settings, "MT5_SERVER_TIMEZONE", "") or "").strip()
        if timezone_name:
            try:
                self._zone = ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                self._invalid_timezone = timezone_name
                logger.warning(
                    "Invalid MT5_SERVER_TIMEZONE '%s': %s. Falling back to offset auto-detection.",
                    timezone_name,
                    exc,
                )

        explicit_minutes = getattr(settings, "MT5_SERVER_UTC_OFFSET_MINUTES", None)
        if explicit_minutes is not None:
            self._offset_seconds = int(explicit_minutes) * 60

    @property
    def offset_seconds(self) -> int:
        return int(self._offset_seconds or 0)

    def describe(self) -> dict[str, Any]:
        explicit = getattr(self.settings, "MT5_SERVER_UTC_OFFSET_MINUTES", None)
        if self._zone is not None:
            source = "iana"
        elif explicit is not None:
            source = "explicit"
        elif self._offset_seconds is not None:
            source = "auto"
        else:
            source = "unsampled"
        warning: str | None = None
        if self._invalid_timezone:
            warning = (
                f"Invalid MT5_SERVER_TIMEZONE '{self._invalid_timezone}'; "
                f"using {source} offset."
            )
        elif source == "unsampled":
            warning = (
                "Broker UTC offset is not configured and no fresh tick has been observed; "
                "exported timestamps may be wrong."
            )
        return {
            "timezone": str(getattr(self.settings, "MT5_SERVER_TIMEZONE", "") or "") or None,
            "utc_offset_minutes": (
                None if self._offset_seconds is None else self._offset_seconds // 60
            ),
            "auto_detected": source == "auto",
            "source": source,
            "warning": warning,
        }

    def observe_from_tick(self, tick: Any | None) -> None:
        """Seed auto-detection from an MT5 tick record when available."""
        if tick is None:
            return
        value = attr(tick, "time_msc", None) or attr(tick, "time", None)
        if value is None:
            return
        seconds = float(value) / 1000.0 if abs(float(value)) > 1e11 else float(value)
        self.observe(seconds)

    def observe(self, broker_epoch_seconds: Any | None) -> None:
        """Estimate the offset from broker timestamps expected to be fresh.

        Several symbols are polled, so the largest reasonable offset seen in a
        short window is the best estimate: the freshest tick has the largest
        difference from real UTC. Differences below half an offset step are
        treated as a zero offset, which keeps stale ticks from being mistaken
        for a negative timezone.
        """
        if broker_epoch_seconds is None or self._zone is not None:
            return
        if getattr(self.settings, "MT5_SERVER_UTC_OFFSET_MINUTES", None) is not None:
            return
        now = time.time()
        try:
            sample_seconds = float(broker_epoch_seconds)
        except (TypeError, ValueError):
            return
        # A broker behind UTC makes a fresh tick look old to the host clock.
        # Accept samples up to MAX_FUTURE_SKEW_SECONDS in either direction;
        # historical samples are excluded by callers, not by this bound.
        if (
            sample_seconds < now - self.MAX_FUTURE_SKEW_SECONDS
            or sample_seconds > now + self.MAX_FUTURE_SKEW_SECONDS
        ):
            return
        new_window = self._offset_seconds is None or (now - self._observed_at) >= 300
        difference = sample_seconds - now
        candidate = int(round(difference / 900.0) * 900)
        if abs(difference) < 675:
            candidate = 0
        if new_window:
            self._offset_seconds = candidate
            self._observed_at = now
            logger.info(
                "Detected broker UTC offset: %d minutes (from MT5 timestamps)",
                candidate // 60,
            )
        elif candidate != self._offset_seconds:
            # Refresh in both directions so DST fall-back is detected too.
            previous = self._offset_seconds
            self._offset_seconds = candidate
            logger.info(
                "Refined broker UTC offset: %d -> %d minutes (from MT5 timestamps)",
                previous // 60,
                candidate // 60,
            )

    def to_utc_epoch(self, broker_epoch_seconds: Any, sample: Any | None = None) -> int:
        if sample is not None:
            self.observe(sample)
        if broker_epoch_seconds is None:
            return 0
        value = float(broker_epoch_seconds)
        if self._zone is not None:
            # MT5 encodes server-local wall time as if it were a Unix epoch.
            naive = datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
            return int(naive.replace(tzinfo=self._zone).timestamp())
        return int(round(value - self.offset_seconds))

    def to_utc_ms(self, broker_epoch_ms: Any, sample: Any | None = None) -> int:
        if sample is not None:
            self.observe(float(sample) / 1000.0 if abs(float(sample)) > 1e11 else sample)
        value = float(broker_epoch_ms)
        seconds = value / 1000.0 if abs(value) > 1e11 else value
        if self._zone is not None:
            naive = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
            utc_dt = naive.replace(tzinfo=self._zone).astimezone(timezone.utc)
            return int(round(utc_dt.timestamp() * 1000.0))
        # Preserve broker millisecond precision instead of truncating to seconds.
        return int(round((seconds - self.offset_seconds) * 1000.0))

    def to_iso(self, broker_epoch_seconds: Any, sample: Any | None = None) -> str | None:
        return timestamp_to_iso(self.to_utc_epoch(broker_epoch_seconds, sample))

    def to_broker_datetime(self, utc_datetime: datetime) -> datetime:
        """Convert an API UTC datetime to an MT5 range argument.

        The MetaTrader5 binding turns *naive* datetimes into epochs using the
        Windows local timezone, which is not the broker timezone. Returning a
        UTC-aware datetime whose wall clock is the broker clock makes the
        binding use the exact broker epoch we want.
        """
        if utc_datetime.tzinfo is None:
            utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
        if self._zone is not None:
            broker_wall = utc_datetime.astimezone(self._zone).replace(tzinfo=None)
        else:
            broker_wall = (utc_datetime + timedelta(seconds=self.offset_seconds)).replace(tzinfo=None)
        return broker_wall.replace(tzinfo=timezone.utc)

    @staticmethod
    def _is_completed(utc_epoch: int, timeframe_seconds: int, timeframe: str | None, now: int) -> bool:
        if timeframe == "MN1":
            opened = datetime.fromtimestamp(utc_epoch, tz=timezone.utc)
            if opened.month == 12:
                next_open = opened.replace(
                    year=opened.year + 1, month=1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                )
            else:
                next_open = opened.replace(
                    month=opened.month + 1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                )
            return int(next_open.timestamp()) <= now
        return utc_epoch + int(timeframe_seconds) <= now

    def convert_candles(
        self,
        candles: list[dict[str, Any]],
        timeframe_seconds: int,
        sample_epoch_seconds: Any | None = None,
        timeframe: str | None = None,
    ) -> list[dict[str, Any]]:
        # Historical samples must never seed the clock; only fresh live ticks do.
        self.observe(sample_epoch_seconds if sample_epoch_seconds is not None else None)
        now = int(time.time())
        for candle in candles:
            utc_epoch = self.to_utc_epoch(candle.get("epoch", 0))
            candle["epoch"] = utc_epoch
            candle["time"] = timestamp_to_iso(utc_epoch)
            candle["completed"] = self._is_completed(
                utc_epoch, timeframe_seconds, timeframe, now
            )
        return candles

    def convert_ticks(
        self,
        ticks: list[dict[str, Any]],
        sample_epoch_ms: Any | None = None,
    ) -> list[dict[str, Any]]:
        self.observe(
            float(sample_epoch_ms) / 1000.0
            if sample_epoch_ms is not None and abs(float(sample_epoch_ms)) > 1e11
            else sample_epoch_ms
        )
        for tick in ticks:
            utc_ms = self.to_utc_ms(tick.get("epoch_ms", 0))
            tick["epoch_ms"] = utc_ms
            tick["timestamp"] = timestamp_to_iso(utc_ms / 1000.0)
        return ticks
