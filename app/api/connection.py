"""MT5 connection-state endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..core.lifecycle import iso_utc
from ..dependencies import AppContext, get_context, require_api_key
from . import ok

router = APIRouter(tags=["system"])


@router.get(
    "/connection",
    summary="MT5 connection details",
    description="Requires API key. Contains no credentials or secrets.",
    dependencies=[Depends(require_api_key)],
)
async def connection_status(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    snapshot = ctx.manager.health()
    snapshot["monitor"] = ctx.monitor.stats()
    return ok(snapshot, checked_at=iso_utc())
