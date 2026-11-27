"""REST and WebSocket API package."""

from __future__ import annotations

from typing import Any

from ..core.context import get_request_id


def ok(data: Any, **meta: Any) -> dict[str, Any]:
    """Wrap successful payloads in the standard response envelope."""
    request_id = get_request_id()
    if request_id and "request_id" not in meta:
        meta["request_id"] = request_id
    return {"success": True, "data": data, "meta": meta}
