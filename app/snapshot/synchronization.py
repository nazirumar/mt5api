"""Pure retrieval-synchronization classification for multi-timeframe snapshots.

Candle close-time alignment, retrieval synchronization, and quote freshness are
reported separately. A snapshot never claims all timeframes were fetched at the
same instant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

DEFAULTS: dict[str, Any] = {
    "SNAPSHOT_SYNC_TIGHT_SECONDS": 2.0,
    "SNAPSHOT_ACCEPTABLE_SYNC_SECONDS": 10.0,
}


def _param(params: Mapping[str, Any] | None, name: str) -> float:
    if params and name in params and params[name] is not None:
        return float(params[name])
    return float(DEFAULTS[name])


def _iso(epoch: float) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def classify_synchronization(
    retrieval_times: Mapping[str, float],
    quote_retrieved_at: float | None,
    quote_freshness: str,
    duration_ms: float | int,
    missing_timeframes: Sequence[str] | None = None,
    latest_candle_epochs: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify how closely the snapshot components were retrieved."""
    tight = _param(params, "SNAPSHOT_SYNC_TIGHT_SECONDS")
    acceptable = _param(params, "SNAPSHOT_ACCEPTABLE_SYNC_SECONDS")
    missing = [str(name) for name in (missing_timeframes or [])]
    epochs = dict(latest_candle_epochs or {})

    samples = [float(value) for value in retrieval_times.values() if value is not None]
    if quote_retrieved_at is not None:
        samples.append(float(quote_retrieved_at))
    span_seconds = round(max(samples) - min(samples), 6) if samples else None

    if not retrieval_times:
        classification = "UNKNOWN"
    elif span_seconds is not None and span_seconds > acceptable:
        classification = "UNSYNCHRONIZED"
    elif quote_freshness in {"VERY_STALE", "UNAVAILABLE"}:
        classification = "UNSYNCHRONIZED"
    elif missing:
        classification = "PARTIALLY_SYNCHRONIZED"
    elif span_seconds is not None and span_seconds <= tight:
        classification = "SYNCHRONIZED"
    else:
        classification = "ACCEPTABLE"

    warnings: list[str] = []
    if span_seconds is not None and span_seconds > tight:
        warnings.append(
            f"Retrieval timestamps span {span_seconds:.3f}s, exceeding the tight "
            f"{tight:.3f}s tolerance."
        )
    if missing:
        warnings.append(f"Missing or failed timeframes: {', '.join(missing)}.")
    if quote_freshness in {"STALE", "VERY_STALE", "UNAVAILABLE"}:
        warnings.append(f"Quote freshness is {quote_freshness}; quote timing is not current.")
    warnings.append(
        "Candle close timestamps differ naturally by timeframe; this is candle-time "
        "alignment, not retrieval synchronization."
    )

    return {
        "classification": classification,
        "duration_ms": round(float(duration_ms), 2),
        "retrieval_span_seconds": span_seconds,
        "tight_tolerance_seconds": tight,
        "acceptable_tolerance_seconds": acceptable,
        "retrieved_at": {key: _iso(value) for key, value in retrieval_times.items()},
        "quote_retrieved_at": _iso(quote_retrieved_at),
        "quote_freshness": quote_freshness,
        "missing_timeframes": missing,
        "candle_time_alignment": {
            "latest_candle_epochs": epochs,
            "note": "Different timeframes close at different UTC times by design.",
        },
        "warnings": warnings,
        "method": "Timestamp spread versus configurable tight/acceptable tolerances",
    }
