"""System health and status endpoints."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ..dependencies import AppContext, get_context, require_api_key
from .. import __version__
from . import ok

router = APIRouter(tags=["system"])


def _freshness(ctx: AppContext) -> tuple[str, str | None]:
    quotes = ctx.cache.all()
    fresh = [q for q in quotes.values() if q.get("is_fresh")]
    if fresh:
        newest = max((q.get("timestamp") or "" for q in fresh), default=None)
        return "live", newest
    if quotes:
        return "stale", max((q.get("timestamp") or "" for q in quotes.values()), default=None)
    return "unknown", None


@router.get(
    "/health",
    summary="Service health",
    description="Public liveness/readiness probe. No API key required.",
)
async def health(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    snapshot = ctx.manager.health()
    freshness, last_tick = _freshness(ctx)
    degraded = not snapshot.get("initialized") or not snapshot.get("terminal_connected")
    return ok(
        {
            "status": "degraded" if degraded else "ok",
            "api_status": "ok",
            "mt5_initialized": snapshot.get("initialized", False),
            "terminal_connected": snapshot.get("terminal_connected", False),
            "broker_clock": ctx.clock.describe(),
            "broker": snapshot.get("broker"),
            "account_server": snapshot.get("server"),
            "last_successful_tick": last_tick or snapshot.get("last_success"),
            "data_freshness": freshness,
            "active_websocket_connections": ctx.subscriptions.active_count(),
            "uptime_seconds": ctx.runtime.uptime_seconds,
            "api_version": __version__,
            "last_error": snapshot.get("last_error"),
        },
        service=ctx.settings.APP_NAME,
    )


@router.get(
    "/status",
    summary="Detailed service status",
    description="Component-level status, collector statistics and safe configuration.",
)
async def status(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    freshness, _ = _freshness(ctx)
    return ok(
        {
            "api": {
                "status": "ok",
                "version": __version__,
                "uptime_seconds": ctx.runtime.uptime_seconds,
                "started_at": ctx.runtime.start_wall_clock,
                "startup_errors": ctx.runtime.startup_errors,
            },
            "mt5": ctx.manager.health(),
            "broker_clock": ctx.clock.describe(),
            "collector": ctx.collector.stats(),
            "monitor": ctx.monitor.stats(),
            "cache": {"symbols": len(ctx.cache.all())},
            "websocket": {
                "clients": ctx.subscriptions.active_count(),
                "subscriptions": ctx.subscriptions.snapshot(),
            },
            "data_freshness": freshness,
            "configuration": ctx.settings.public_summary(),
        }
    )
