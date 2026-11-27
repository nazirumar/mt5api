"""FastAPI application factory and lifecycle."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import ok
from .api.router import api_router, websocket_router
from .config import Settings, get_settings
from .core.context import get_request_id, new_request_id, request_id_var
from .core.exceptions import ServiceError
from .core.lifecycle import RuntimeState, iso_utc
from .core.logging import configure_logging, get_logger
from .database.repositories import record_event, recent_events
from .database.session import create_database
from .dependencies import AppContext, require_api_key
from .mt5.account import AccountService
from .mt5.candles import CandleService
from .mt5.clock import BrokerClock
from .mt5.connection import MT5ConnectionManager
from .mt5.quotes import QuoteService
from .mt5.symbols import SymbolService
from .mt5.ticks import TickService
from .services.cache import QuoteCache
from .services.candle_cache import CandleCache
from .services.connection_monitor import ConnectionMonitor
from .services.history_limiter import HistoryLimiter
from .services.market_collector import MarketCollector
from .services.subscription_manager import SubscriptionManager

logger = get_logger("nazbeen_mt5.app")

DESCRIPTION = """
Permanent local market-data service for MetaTrader 5.

## Authentication
Protected endpoints require the API key in the `X-API-Key` header.
WebSocket clients may authenticate with `?api_key=<key>` (deprecated), the
configured header, or an `auth` message as the first frame. `/api/v1/health`
is public; `/api/v1/ready` requires a key.

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
        candles = CandleService(manager, settings, clock)
        cache = QuoteCache()
        candle_cache = CandleCache()
        subscriptions = SubscriptionManager(
            stale_timeout=settings.WEBSOCKET_STALE_TIMEOUT
        )
        history_limiter = HistoryLimiter(settings)
        collector = MarketCollector(
            settings, manager, quotes, candles, symbols, cache, candle_cache,
            subscriptions, database["session_factory"],
        )
        monitor = ConnectionMonitor(settings, manager, collector)
        context = AppContext(
            settings=settings,
            runtime=runtime,
            manager=manager,
            clock=clock,
            symbols=symbols,
            quotes=quotes,
            candles=candles,
            ticks=TickService(manager, settings, clock),
            account=AccountService(manager, settings),
            cache=cache,
            candle_cache=candle_cache,
            subscriptions=subscriptions,
            collector=collector,
            monitor=monitor,
            history_limiter=history_limiter,
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
        request_id = request.headers.get("X-Request-ID", "").strip() or new_request_id()
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.monotonic()
        try:
            content_length = request.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > 1_048_576:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "success": False,
                        "error": {
                            "code": "PAYLOAD_TOO_LARGE",
                            "message": "Request body too large",
                        },
                        "meta": {"request_id": request_id},
                    },
                )
            else:
                response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Request-ID", request_id)
        logger.info(
            "HTTP %s %s -> %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
                "meta": {"timestamp": iso_utc(), "request_id": get_request_id()},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {"code": "VALIDATION_ERROR", "message": "Invalid request parameters", "details": exc.errors()},
                "meta": {"request_id": get_request_id()},
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "success": False,
                "error": detail,
                "meta": {"request_id": get_request_id()},
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled API error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"},
                "meta": {"request_id": get_request_id()},
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

    @app.get(
        "/api/v1/events",
        tags=["system"],
        dependencies=[Depends(require_api_key)],
        summary="Recent service events",
        description="Authenticated diagnostic events with optional kind/level/request_id filters.",
    )
    async def events(
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
        kind: Annotated[str | None, Query(description="Exact event kind")] = None,
        level: Annotated[str | None, Query(description="INFO, WARNING or ERROR")] = None,
        request_id: Annotated[str | None, Query(description="Correlation ID from X-Request-ID")] = None,
    ) -> dict[str, Any]:
        context: AppContext = app.state.context
        records = recent_events(
            context.database["session_factory"],
            limit=limit,
            kind=kind,
            level=level,
            request_id=request_id,
        )
        return ok(records, count=len(records))

    app.include_router(api_router)
    app.include_router(websocket_router)
    return app


app = create_app()
