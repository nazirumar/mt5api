"""Pure snapshot-level data-quality aggregation.

This score reflects completeness and reliability of the returned snapshot only.
It is not a win probability, forecast, or buy/sell confidence.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

DEFAULTS: dict[str, Any] = {
    "CONTEXT_DATA_QUALITY_MIN_SCORE": 50,
    "CONTEXT_DATA_QUALITY_GOOD_SCORE": 75,
    "CONTEXT_DATA_QUALITY_EXCELLENT_SCORE": 90,
}


def _param(params: Mapping[str, Any] | None, name: str) -> int:
    if params and name in params and params[name] is not None:
        return int(params[name])
    return int(DEFAULTS[name])


def assess_snapshot_quality(
    timeframe_qualities: Mapping[str, Mapping[str, Any]],
    requested: Sequence[str],
    quote: Mapping[str, Any],
    connected: bool,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate per-timeframe quality into a bounded snapshot score."""
    requested_names = [str(item) for item in requested]
    available = [name for name in requested_names if name in timeframe_qualities]
    missing = [name for name in requested_names if name not in timeframe_qualities]
    scores = [int(item.get("score") or 0) for item in timeframe_qualities.values()]
    base_score = sum(scores) / len(scores) if scores else 0.0
    completeness = len(available) / len(requested_names) if requested_names else 0.0
    score = int(round(base_score * completeness))

    quote_freshness = str(quote.get("freshness") or "UNKNOWN")
    quote_available = bool(
        quote.get("market_available") and quote.get("bid") is not None and quote.get("ask") is not None
    )
    quote_ok = quote_available and quote_freshness in {"FRESH", "STALE"}
    unusable_quote = quote_freshness in {"VERY_STALE", "UNAVAILABLE"}
    unusable_timeframes = [
        name
        for name, item in timeframe_qualities.items()
        if str(item.get("grade") or "").upper() == "UNUSABLE" or item.get("usable") is False
    ]
    usable = bool(
        connected and quote_ok and available and not unusable_quote and not unusable_timeframes
    )

    checks = {
        "mt5_connected": bool(connected),
        "quote_available": quote_available,
        "quote_fresh": quote_freshness == "FRESH",
        "all_timeframes_available": not missing,
        "timeframes_available": bool(available),
        "timeframe_data_usable": not unusable_timeframes,
    }
    warnings: list[str] = []
    if not connected:
        warnings.append("MT5 terminal connection is unavailable.")
    if unusable_quote or not quote_available:
        warnings.append("Live quote is unavailable or too stale to trust.")
    elif quote_freshness == "STALE":
        warnings.append("Quote is stale; context describes cached quote data.")
    if missing:
        warnings.append(f"Missing or failed timeframes: {', '.join(missing)}.")
    if available and completeness < 1:
        warnings.append("Snapshot is partial; alignment covers only available timeframes.")
    if unusable_timeframes:
        warnings.append(
            "At least one timeframe has unusable data: " + ", ".join(unusable_timeframes) + "."
        )

    excellent = _param(params, "CONTEXT_DATA_QUALITY_EXCELLENT_SCORE")
    good = _param(params, "CONTEXT_DATA_QUALITY_GOOD_SCORE")
    minimum = _param(params, "CONTEXT_DATA_QUALITY_MIN_SCORE")
    if not usable and (
        not available
        or not connected
        or unusable_quote
        or not quote_available
        or unusable_timeframes
    ):
        grade = "UNUSABLE"
    elif score >= excellent:
        grade = "EXCELLENT"
    elif score >= good:
        grade = "GOOD"
    elif score >= minimum:
        grade = "FAIR"
    else:
        grade = "POOR"

    return {
        "score": score,
        "grade": grade,
        "usable": usable,
        "method": "Mean timeframe quality scaled by completeness; not prediction confidence",
        "disclaimer": "This score is not a win probability, forecast, or buy/sell confidence.",
        "checks": checks,
        "warnings": warnings,
        "missing_timeframes": missing,
        "available_timeframes": available,
        "completeness": round(completeness, 4),
        "quote_freshness": quote_freshness,
    }
