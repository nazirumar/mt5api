"""Public liveness and authenticated readiness endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from .. import __version__
from ..dependencies import AppContext, get_context, require_api_key
from . import ok

router = APIRouter(tags=["system"])


def _stale_window(ctx: AppContext) -> float:
    return ctx.settings.MARKET_POLL_INTERVAL * ctx.settings.QUOTE_STALE_MULTIPLIER


def _freshness(ctx: AppContext) -> tuple[str, str | None]:
    """Compute freshness now rather than trusting a cached poll-time flag."""
    quotes = ctx.cache.all_fresh(_stale_window(ctx))
    fresh = [q for q in quotes.values() if q.get("is_fresh")]
    if fresh:
        newest = max((q.get("timestamp") or "" for q in fresh), default=None)
        return "live", newest or None
    if quotes:
        newest = max((q.get("timestamp") or "" for q in quotes.values()), default=None)
        return "stale", newest or None
    return "unknown", None


_PUBLIC_MT5_FIELDS = (
    "initialized",
    "terminal_connected",
    "last_success",
    "reconnect_attempts",
)


def _public_mt5_health(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drop terminal, broker and account identifiers from the public status."""
    return {key: snapshot.get(key) for key in _PUBLIC_MT5_FIELDS}


@router.get(
    "/health",
    summary="Service liveness",
    description="Public liveness probe. Use /ready for authenticated MT5 readiness detail.",
)
async def health(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    snapshot = await asyncio.to_thread(ctx.manager.health)
    freshness, last_tick = _freshness(ctx)
    degraded = not snapshot.get("initialized") or not snapshot.get("terminal_connected")
    return ok(
        {
            "status": "degraded" if degraded else "ok",
            "api_status": "ok",
            "mt5_initialized": snapshot.get("initialized", False),
            "terminal_connected": snapshot.get("terminal_connected", False),
            "broker_clock": ctx.clock.describe(),
            "data_freshness": freshness,
            "last_successful_tick": last_tick,
            "uptime_seconds": ctx.runtime.uptime_seconds,
            "api_version": __version__,
        },
        service=ctx.settings.APP_NAME,
    )


@router.get(
    "/ready",
    summary="Authenticated MT5 readiness",
    description="Component-level readiness, broker clock, collector and WebSocket state.",
    dependencies=[Depends(require_api_key)],
)
async def ready(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    snapshot = await asyncio.to_thread(ctx.manager.health)
    freshness, last_tick = _freshness(ctx)
    ready_state = bool(snapshot.get("initialized") and snapshot.get("terminal_connected"))
    return ok(
        {
            "ready": ready_state,
            "status": "ok" if ready_state else "degraded",
            "mt5": snapshot,
            "broker_clock": ctx.clock.describe(),
            "collector": ctx.collector.stats(),
            "history_limiter": ctx.history_limiter.stats(),
            "websocket": {
                "clients": ctx.subscriptions.active_count(),
                "subscriptions": ctx.subscriptions.snapshot(),
            },
            "data_freshness": freshness,
            "last_successful_tick": last_tick,
        }
    )


@router.get(
    "/status",
    summary="Detailed service status",
    description="Component-level status, collector statistics and safe configuration.",
)
async def status(
    request: Request,
    verbose: Annotated[bool, Query(description="Include full component snapshots")] = True,
) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    snapshot = await asyncio.to_thread(ctx.manager.health)
    freshness, _ = _freshness(ctx)
    payload: dict[str, Any] = {
        "api": {
            "status": "ok",
            "version": __version__,
            "uptime_seconds": ctx.runtime.uptime_seconds,
            "started_at": ctx.runtime.start_wall_clock,
            "startup_errors": ctx.runtime.startup_errors,
        },
        "mt5": _public_mt5_health(snapshot),
        "broker_clock": ctx.clock.describe(),
        "collector": ctx.collector.stats(),
        "monitor": ctx.monitor.stats(),
        "cache": {"symbols": len(ctx.cache.all())},
        "websocket": {
            "clients": ctx.subscriptions.active_count(),
            "subscriptions": [],
        },
        "data_freshness": freshness,
        "configuration": ctx.settings.public_summary(),
    }
    if verbose:
        payload["history_limiter"] = ctx.history_limiter.stats()
    return ok(payload)
