"""FastAPI application factory and lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import ok
from .api.router import api_router, websocket_router
from .config import Settings, get_settings
from .core.exceptions import ServiceError
from .core.lifecycle import RuntimeState, iso_utc
from .core.logging import configure_logging, get_logger
from .database.repositories import record_event, recent_events
from .database.session import create_database
from .dependencies import AppContext
from .mt5.account import AccountService
from .mt5.candles import CandleService
from .mt5.clock import BrokerClock
from .mt5.connection import MT5ConnectionManager
from .mt5.quotes import QuoteService
from .mt5.symbols import SymbolService
from .mt5.ticks import TickService
from .services.cache import QuoteCache
from .services.connection_monitor import ConnectionMonitor
from .services.market_collector import MarketCollector
from .services.subscription_manager import SubscriptionManager

logger = get_logger("nazbeen_mt5.app")

DESCRIPTION = """
Permanent local market-data service for MetaTrader 5.

## Authentication
Protected endpoints require the API key in the `X-API-Key` header.
WebSocket clients pass `?api_key=<key>` (or the configured header).

## Notes
- All symbol names are broker-native; no symbol substitution is performed.
- Historical timestamps are normalized to UTC; forming candles are marked.
- This service is read-only: no trading operations are implemented.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logger = configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = create_database(settings)
        runtime = RuntimeState()
        manager = MT5ConnectionManager(settings)
        clock = BrokerClock(settings)
        symbols = SymbolService(manager, settings)
        quotes = QuoteService(manager, settings, clock)
        cache = QuoteCache()
        subscriptions = SubscriptionManager(
            stale_timeout=settings.WEBSOCKET_STALE_TIMEOUT
        )
        collector = MarketCollector(
            settings, manager, quotes, symbols, cache, subscriptions,
            database["session_factory"],
        )
        monitor = ConnectionMonitor(settings, manager, collector)
        context = AppContext(
            settings=settings,
            runtime=runtime,
            manager=manager,
            clock=clock,
            symbols=symbols,
            quotes=quotes,
            candles=CandleService(manager, settings, clock),
            ticks=TickService(manager, settings, clock),
            account=AccountService(manager, settings),
            cache=cache,
            subscriptions=subscriptions,
            collector=collector,
            monitor=monitor,
            database=database,
        )
        app.state.context = context

        record_event(database["session_factory"], "startup", "API starting")
        # Initialization always happens at startup; DISABLE_BACKGROUND only
        # suppresses the long-running collector/monitor loops (useful in tests).
        initialized = await manager.initialize_async()
        if not initialized:
            runtime.startup_errors.append("MT5 initialization failed")
            record_event(
                database["session_factory"], "connection",
                "MT5 initialization failed", level="WARNING",
            )
        if not settings.DISABLE_BACKGROUND:
            await collector.start()
            await monitor.start()
        try:
            yield
        finally:
            await monitor.stop()
            await collector.stop()
            manager.shutdown()
            record_event(database["session_factory"], "shutdown", "API stopped")
            database["engine"].dispose()

    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=[settings.API_KEY_HEADER, "Content-Type"],
    )

    @app.middleware("http")
    async def request_guards(request: Request, call_next: Any):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 1_048_576:
            return JSONResponse(
                status_code=413,
                content={"success": False, "error": {"code": "PAYLOAD_TOO_LARGE", "message": "Request body too large"}},
            )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
                "meta": {"timestamp": iso_utc()},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {"code": "VALIDATION_ERROR", "message": "Invalid request parameters", "details": exc.errors()},
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={"success": False, "error": detail},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled API error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"},
            },
        )

    @app.get("/", tags=["system"], summary="Service overview")
    async def root() -> dict[str, Any]:
        return ok(
            {
                "name": settings.APP_NAME,
                "version": __version__,
                "docs": "/docs",
                "health": "/api/v1/health",
                "websocket": "/ws/market",
                "note": "Local-only read-only market data service",
            }
        )

    @app.get("/api/v1/events", tags=["system"], include_in_schema=False)
    async def events(limit: int = 20) -> dict[str, Any]:
        context: AppContext = app.state.context
        return ok(recent_events(context.database["session_factory"], limit=limit))

    app.include_router(api_router)
    app.include_router(websocket_router)
    return app


app = create_app()
