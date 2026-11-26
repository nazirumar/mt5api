"""REST and WebSocket API package."""

from __future__ import annotations

from typing import Any


def ok(data: Any, **meta: Any) -> dict[str, Any]:
    """Wrap successful payloads in the standard response envelope."""
    return {"success": True, "data": data, "meta": meta}
