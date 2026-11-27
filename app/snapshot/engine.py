"""Orchestration service for deterministic multi-timeframe snapshots.

The engine is synchronous on purpose: the route calls it through
``asyncio.to_thread``. It retrieves timeframes sequentially, reuses the Phase 18
data/analytics engine, and never opens a new MT5 connection or spawns a thread
per timeframe.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..context import analytics
from ..context.sessions import session_context
from ..core.exceptions import InvalidRequestError, ServiceError
from ..core.helpers import timestamp_to_iso
from ..mt5.timeframes import TIMEFRAME_SECONDS, normalize_timeframe
from . import alignment, quality, roles, synchronization

DISCLAIMER = (
    "Snapshot classifications describe past and current market data only. "
    "They are not trading signals, probabilities, predictions, or guarantees."
)
RETRIEVAL_ERROR_CODE = "TIMEFRAME_RETRIEVAL_FAILED"


class SnapshotEngine:
    """Build a bounded, multi-timeframe snapshot from real MT5 data."""

    def __init__(self, settings: Any, context_engine: Any) -> None:
        self.settings = settings
        self.context_engine = context_engine

    def _param(self, name: str, default: Any) -> Any:
        return getattr(self.settings, name, default)

    def _parse_timeframes(self, raw: str | None) -> list[str]:
        configured = raw if raw is not None else str(self._param("SNAPSHOT_DEFAULT_TIMEFRAMES", "M1,M5,M15,H1,H4,D1"))
        raw_text = str(configured)
        split_parts = raw_text.split(",")
        if len(split_parts) > 1 and any(not item.strip() for item in split_parts):
            raise InvalidRequestError(
                "timeframes must not contain empty entries",
                details={"parameter": "timeframes"},
            )
        parts = [item.strip() for item in split_parts if item.strip()]
        if not parts:
            raise InvalidRequestError(
                "At least one timeframe is required",
                details={"parameter": "timeframes"},
            )
        normalized: list[str] = []
        for part in parts:
            timeframe = normalize_timeframe(part)
            if timeframe in normalized:
                raise InvalidRequestError(
                    f"Duplicate timeframe '{timeframe}' is not allowed",
                    details={"timeframe": timeframe},
                )
            normalized.append(timeframe)
        maximum = int(self._param("SNAPSHOT_MAX_TIMEFRAMES", 7))
        if len(normalized) > maximum:
            raise InvalidRequestError(
                f"A maximum of {maximum} timeframes is supported",
                details={"max_timeframes": maximum},
            )
        return normalized

    def _validate_limit(self, limit: int | None) -> int:
        configured = limit if limit is not None else int(self._param("SNAPSHOT_DEFAULT_LIMIT", 100))
        try:
            value = int(configured)
        except (TypeError, ValueError):
            raise InvalidRequestError("limit must be an integer", details={"parameter": "limit"}) from None
        maximum = int(self._param("SNAPSHOT_MAX_LIMIT", 500))
        if value <= 0 or value > maximum:
            raise InvalidRequestError(
                f"limit must be between 1 and {maximum}",
                details={"max_limit": maximum},
            )
        return value

    @staticmethod
    def _epoch(candle: Mapping[str, Any]) -> float:
        value = candle.get("epoch")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        text = candle.get("time")
        if isinstance(text, str):
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        return float("inf")

    def _normalize_series(
        self, series: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Return chronological, duplicate-free candles; never invent data."""
        items = [dict(item) for item in series if isinstance(item, Mapping)]
        original_epochs = [self._epoch(item) for item in items]
        ordered = sorted(items, key=self._epoch)
        reordered = original_epochs != [self._epoch(item) for item in ordered]
        seen: set[Any] = set()
        result: list[dict[str, Any]] = []
        duplicates = 0
        for candle in ordered:
            marker = candle.get("epoch") if candle.get("epoch") is not None else candle.get("time")
            key = ("epoch", marker) if marker is not None else ("index", len(result))
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            result.append(candle)
        return result, duplicates, reordered

    @staticmethod
    def _completion(candle: Mapping[str, Any] | None) -> str:
        if not candle:
            return "UNKNOWN"
        completed = candle.get("completed")
        if completed is True:
            return "COMPLETED"
        if completed is False:
            return "FORMING"
        return "UNKNOWN"

    def _candle_freshness(
        self, latest_epoch: float | None, timeframe: str, now_epoch: int
    ) -> str:
        if latest_epoch is None:
            return "UNKNOWN"
        duration = max(1, int(TIMEFRAME_SECONDS.get(timeframe, 60)))
        age = max(0.0, float(now_epoch) - float(latest_epoch))
        current = float(self._param("SNAPSHOT_CANDLE_FRESH_CURRENT_MULTIPLIER", 2.0))
        stale = float(self._param("SNAPSHOT_CANDLE_FRESH_STALE_MULTIPLIER", 5.0))
        if age <= duration * current:
            return "CURRENT"
        if age <= duration * stale:
            return "LAGGING"
        return "STALE"

    @staticmethod
    def _error_details(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, ServiceError):
            return exc.code, exc.message
        # Do not leak internal exception text in a client-facing response.
        return RETRIEVAL_ERROR_CODE, f"Timeframe data could not be retrieved ({type(exc).__name__})"

    def build_snapshot(
        self,
        symbol: str,
        timeframes: str | None = None,
        limit: int | None = None,
        include_context: bool = True,
        include_candles: bool = True,
        include_indicators: bool = True,
        include_structure: bool = True,
        request_id: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        started_perf = time.perf_counter()
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_utc = current.astimezone(timezone.utc)
        now_epoch = int(current_utc.timestamp())

        requested = self._parse_timeframes(timeframes)
        wanted_limit = self._validate_limit(limit)
        return_cap = int(self._param("SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME", 100))

        info = self.context_engine.resolve_symbol(symbol)
        quote = self.context_engine.quote_snapshot(info["symbol"], info, now_epoch)
        quote_retrieved_at = time.time()
        connected = bool(self.context_engine.connected())
        broker_time = self.context_engine.broker_time()
        session = dict(session_context(current_utc, broker_time, self.context_engine.params))

        warnings: list[str] = []
        retrieval_times: dict[str, float] = {}
        timeframes_map: dict[str, dict[str, Any]] = {}

        for timeframe in requested:
            timeframe_started = time.time()
            metadata: dict[str, Any] = {
                "timeframe": timeframe,
                "duration_seconds": int(TIMEFRAME_SECONDS.get(timeframe, 0)),
                "requested_candles": wanted_limit,
                "returned_candles": 0,
                "available_candles": 0,
                "latest_candle_time": None,
                "latest_candle_epoch": None,
                "latest_candle_completion": "UNKNOWN",
                "data_availability": False,
                "freshness": "UNKNOWN",
                "role": roles.timeframe_role(timeframe),
                "retrieved_at": None,
                "retrieval_duration_ms": None,
            }
            entry: dict[str, Any] = {
                "status": "OK",
                "metadata": metadata,
                "candles": [],
                "context": {"available": False, "reason": "Context not requested", "session": dict(session)},
                "data_quality": {},
            }
            try:
                raw_series = self.context_engine.series(info["symbol"], timeframe, wanted_limit, info)
                series, duplicates, reordered = self._normalize_series(raw_series)
                if duplicates:
                    warnings.append(f"{timeframe}: {duplicates} duplicate candle timestamp(s) removed.")
                if reordered:
                    warnings.append(f"{timeframe}: candles were returned out of order and were sorted chronologically.")

                latest = series[-1] if series else None
                latest_epoch = self._epoch(latest) if latest is not None else None
                metadata.update(
                    {
                        "available_candles": len(series),
                        "latest_candle_time": latest.get("time") if latest else None,
                        "latest_candle_epoch": int(latest_epoch) if latest_epoch not in (None, float("inf")) else None,
                        "latest_candle_completion": self._completion(latest),
                        "data_availability": bool(series),
                        "freshness": self._candle_freshness(latest_epoch, timeframe, now_epoch),
                    }
                )
                candles = [dict(item) for item in series[-return_cap:]] if include_candles else []
                metadata["returned_candles"] = len(candles)
                entry["candles"] = candles

                if include_context:
                    context = self.context_engine.context_for_series(
                        series, quote, include_indicators, include_structure, False
                    )
                    context["session"] = dict(session)
                    entry["context"] = context
                entry["data_quality"] = self.context_engine.quality_for_series(
                    info, quote, series, connected
                )
            except Exception as exc:  # partial snapshot: record and continue
                code, message = self._error_details(exc)
                entry["status"] = "ERROR"
                entry["error"] = {"code": code, "message": message}
                entry["context"] = {
                    "available": False,
                    "reason": "Timeframe retrieval failed",
                    "session": dict(session),
                }
                entry["data_quality"] = {
                    "score": 0,
                    "grade": "UNUSABLE",
                    "usable": False,
                    "warnings": [message],
                    "missing_data": [f"{timeframe} retrieval"],
                }
                warnings.append(f"{timeframe}: {message}")

            retrieved = time.time()
            retrieval_times[timeframe] = retrieved
            metadata["retrieved_at"] = timestamp_to_iso(retrieved)
            metadata["retrieval_duration_ms"] = round((retrieved - timeframe_started) * 1000, 2)
            timeframes_map[timeframe] = entry

        successful = [name for name, entry in timeframes_map.items() if entry["status"] != "ERROR"]
        failed = [name for name, entry in timeframes_map.items() if entry["status"] == "ERROR"]
        empty = [name for name in successful if not timeframes_map[name]["metadata"]["available_candles"]]
        if empty:
            warnings.append(f"No candles returned for: {', '.join(empty)}.")

        quality_result = quality.assess_snapshot_quality(
            {
                name: timeframes_map[name]["data_quality"]
                for name in successful
                if isinstance(timeframes_map[name].get("data_quality"), Mapping)
                and timeframes_map[name]["data_quality"]
            },
            requested,
            quote,
            connected,
            self.context_engine.params,
        )

        if not quality_result["usable"]:
            for name in successful:
                context = timeframes_map[name].get("context")
                if isinstance(context, dict) and context:
                    timeframes_map[name]["context"] = analytics.unknown_classifications(context)
            warnings.append("Context classifications suppressed because snapshot data quality is unusable.")

        alignment_items = []
        for name in requested:
            entry = timeframes_map[name]
            trend = entry.get("context", {}).get("trend", {}) if isinstance(entry.get("context"), dict) else {}
            alignment_items.append(
                {
                    "timeframe": name,
                    "classification": trend.get("classification", "UNKNOWN") if isinstance(trend, dict) else "UNKNOWN",
                    "duration_seconds": entry["metadata"].get("duration_seconds", 0),
                    "available": entry["status"] != "ERROR",
                    "role": roles.timeframe_role(name),
                }
            )
        alignment_result = alignment.analyze_alignment(alignment_items, self.context_engine.params, requested)

        duration_ms = round((time.perf_counter() - started_perf) * 1000, 2)
        synchronization_result = synchronization.classify_synchronization(
            retrieval_times,
            quote_retrieved_at,
            str(quote.get("freshness") or "UNKNOWN"),
            duration_ms,
            failed,
            {name: entry["metadata"].get("latest_candle_epoch") for name, entry in timeframes_map.items()},
            self.context_engine.params,
        )

        if not successful:
            status = "UNAVAILABLE"
        elif failed or empty or not quality_result["usable"]:
            status = "PARTIAL"
        else:
            status = "COMPLETE"

        combined_warnings = list(
            dict.fromkeys(
                warnings + list(quality_result.get("warnings") or []) + list(synchronization_result.get("warnings") or [])
            )
        )

        return {
            "snapshot_id": uuid.uuid4().hex,
            "symbol": info["symbol"],
            "generated_at": timestamp_to_iso(now_epoch),
            "snapshot_status": status,
            "synchronization": synchronization_result,
            "quote": dict(quote),
            "timeframes": timeframes_map,
            "alignment": alignment_result,
            "session": session,
            "data_quality": quality_result,
            "warnings": combined_warnings,
            "meta": {
                "request_id": request_id,
                "engine_version": "1.0.0",
                "method": "deterministic_mtf_snapshot_v1",
                "phase18_method": "deterministic_rule_set_v1",
                "timeframes": requested,
                "limit": wanted_limit,
                "include_context": bool(include_context),
                "include_candles": bool(include_candles),
                "include_indicators": bool(include_indicators),
                "include_structure": bool(include_structure),
                "disclaimer": DISCLAIMER,
            },
        }
