"""Small conversion helpers shared by MT5 adapters and services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from dicts, objects, and NumPy structured records."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)

    # MetaTrader5 returns NumPy structured scalars/arrays. In recent NumPy
    # versions these expose fields by key but not always by attribute.
    dtype = getattr(obj, "dtype", None)
    names = getattr(dtype, "names", None)
    if names and name in names:
        try:
            value = obj[name]
            return value.item() if hasattr(value, "item") else value
        except (KeyError, IndexError, TypeError):
            pass

    if hasattr(obj, name):
        return getattr(obj, name, default)
    getter = getattr(obj, "get", None)
    if callable(getter):
        try:
            return getter(name, default)
        except (TypeError, KeyError):
            return default
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def timestamp_to_iso(value: Any) -> str | None:
    """Convert MT5 epoch seconds/ms or datetime to an ISO-8601 UTC string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        number = float(value)
        if abs(number) > 1e11:  # milliseconds
            number /= 1000.0
        try:
            dt = datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if hasattr(record, "_asdict"):
        return dict(record._asdict())
    if hasattr(record, "__dict__"):
        return dict(vars(record))
    if hasattr(record, "dtype") and hasattr(record, "tolist"):
        try:
            return {name: record[name].item() if hasattr(record[name], "item") else record[name]
                    for name in record.dtype.names or ()}
        except Exception:  # pragma: no cover
            return {}
    return {}
