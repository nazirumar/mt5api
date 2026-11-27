"""Deterministic cross-timeframe alignment analysis.

This module is pure: no I/O, no clock, no MT5 access. Counts and ratios describe
agreement between Phase 18 trend classifications only. They are not signals,
probabilities, or expected trade outcomes.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

DEFAULTS: dict[str, Any] = {
    "SNAPSHOT_MIN_ALIGNED_TIMEFRAMES": 2,
    "SNAPSHOT_ALIGNMENT_DOMINANT_RATIO": 0.6,
    "SNAPSHOT_CONFLICT_REVERSALS": 1,
}

DIRECTIONS = ("BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNKNOWN")


def _param(params: Mapping[str, Any] | None, name: str) -> Any:
    if params and name in params and params[name] is not None:
        return params[name]
    return DEFAULTS[name]


def direction_of(classification: Any) -> str:
    """Normalize a Phase 18 classification into an alignment direction."""
    text = str(classification or "").strip().upper()
    if text.endswith("BULLISH"):
        return "BULLISH"
    if text.endswith("BEARISH"):
        return "BEARISH"
    if text in {"NEUTRAL", "MIXED", "UNKNOWN"}:
        return text
    return "UNKNOWN"


def analyze_alignment(
    items: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any] | None = None,
    requested: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Summarize directional agreement across successfully retrieved timeframes."""
    minimum = int(_param(params, "SNAPSHOT_MIN_ALIGNED_TIMEFRAMES"))
    dominant_required = float(_param(params, "SNAPSHOT_ALIGNMENT_DOMINANT_RATIO"))
    conflict_required = int(_param(params, "SNAPSHOT_CONFLICT_REVERSALS"))

    requested_order = [str(item).strip().upper() for item in (requested or []) if str(item).strip()]
    by_timeframe: dict[str, dict[str, Any]] = {}
    for item in items:
        timeframe = str(item.get("timeframe") or "").strip().upper()
        if timeframe:
            by_timeframe[timeframe] = dict(item)
    order = requested_order or [str(item.get("timeframe") or "").strip().upper() for item in items]
    order = [name for name in order if name]

    details: list[dict[str, Any]] = []
    counts = {name: 0 for name in DIRECTIONS}
    for index, timeframe in enumerate(order):
        item = by_timeframe.get(timeframe, {})
        available = bool(item.get("available", False))
        direction = direction_of(item.get("classification", "UNKNOWN")) if available else "UNKNOWN"
        counts[direction] += 1
        details.append(
            {
                "timeframe": timeframe,
                "role": item.get("role"),
                "duration_seconds": item.get("duration_seconds"),
                "classification": item.get("classification", "UNKNOWN") if available else "UNKNOWN",
                "direction": direction,
                "available": available,
                "requested_index": index,
            }
        )

    analyzed = counts["BULLISH"] + counts["BEARISH"] + counts["NEUTRAL"] + counts["MIXED"]
    directional = counts["BULLISH"] + counts["BEARISH"]
    alignment_ratio = max(counts[name] for name in DIRECTIONS[:4]) / analyzed if analyzed else None
    dominant = max(DIRECTIONS[:4], key=lambda name: (counts[name], -DIRECTIONS.index(name)))

    ordered_directional = sorted(
        (entry for entry in details if entry["direction"] in {"BULLISH", "BEARISH"}),
        key=lambda entry: (-(int(entry.get("duration_seconds") or 0)), entry["requested_index"]),
    )
    conflicts: list[dict[str, Any]] = []
    for higher, lower in zip(ordered_directional, ordered_directional[1:]):
        if higher["direction"] != lower["direction"]:
            conflicts.append(
                {
                    "higher_timeframe": higher["timeframe"],
                    "lower_timeframe": lower["timeframe"],
                    "higher_classification": higher["classification"],
                    "lower_classification": lower["classification"],
                    "note": "Opposite directional classifications on adjacent available timeframes",
                }
            )

    if analyzed < minimum:
        classification = "INSUFFICIENT_DATA"
    elif directional == 0:
        classification = "UNKNOWN"
    elif len(conflicts) >= conflict_required and (alignment_ratio or 0.0) < dominant_required:
        classification = "CONFLICTING"
    elif directional == analyzed and counts["BULLISH"] and not counts["BEARISH"]:
        classification = "FULLY_ALIGNED"
    elif directional == analyzed and counts["BEARISH"] and not counts["BULLISH"]:
        classification = "FULLY_ALIGNED"
    elif dominant in {"BULLISH", "BEARISH"} and (alignment_ratio or 0.0) >= dominant_required:
        classification = "MOSTLY_ALIGNED"
    else:
        classification = "MIXED"

    return {
        "classification": classification,
        "bullish_count": counts["BULLISH"],
        "bearish_count": counts["BEARISH"],
        "neutral_count": counts["NEUTRAL"],
        "mixed_count": counts["MIXED"],
        "unknown_count": counts["UNKNOWN"],
        "analyzed_count": analyzed,
        "directional_count": directional,
        "alignment_ratio": round(alignment_ratio, 4) if alignment_ratio is not None else None,
        "dominant_classification": dominant if analyzed else "UNKNOWN",
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "details": details,
        "incomplete": any(not entry["available"] or entry["direction"] == "UNKNOWN" for entry in details),
        "method": "Deterministic counts of Phase 18 trend classifications; higher timeframe not assumed correct",
        "disclaimer": (
            "Alignment describes agreement among historical/current classifications. "
            "It is not a BUY/SELL signal, win probability, or expected trade success."
        ),
    }
