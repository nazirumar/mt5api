"""Small repository helpers; all writes are short and infrequent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from .models import CollectorStat, ServiceEvent, SymbolMetadata


def record_event(
    session_factory: Any,
    kind: str,
    message: str,
    *,
    level: str = "INFO",
    details: dict | None = None,
) -> None:
    try:
        with session_factory() as session:
            session.add(
                ServiceEvent(kind=kind, level=level, message=message[:1000], details=details)
            )
            session.commit()
    except Exception:  # pragma: no cover - diagnostics must never break the API
        pass


def recent_events(session_factory: Any, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
    try:
        with session_factory() as session:
            statement = select(ServiceEvent).order_by(ServiceEvent.timestamp.desc()).limit(limit)
            if kind:
                statement = statement.where(ServiceEvent.kind == kind)
            rows = session.scalars(statement).all()
            return [
                {
                    "timestamp": row.timestamp.isoformat(),
                    "kind": row.kind,
                    "level": row.level,
                    "message": row.message,
                    "details": row.details,
                }
                for row in rows
            ]
    except Exception:  # pragma: no cover
        return []


def upsert_symbol(session_factory: Any, symbol: dict[str, Any]) -> None:
    try:
        with session_factory() as session:
            existing = session.get(SymbolMetadata, symbol["symbol"])
            if existing is None:
                session.add(
                    SymbolMetadata(
                        symbol=symbol["symbol"],
                        description=symbol.get("description"),
                        category=symbol.get("category"),
                        digits=symbol.get("digits", 0),
                        point=symbol.get("point", 0.0),
                    )
                )
            else:
                existing.description = symbol.get("description")
                existing.category = symbol.get("category")
                existing.digits = symbol.get("digits", 0)
                existing.point = symbol.get("point", 0.0)
                existing.updated_at = datetime.now(timezone.utc)
            session.commit()
    except Exception:  # pragma: no cover
        pass


def record_collector_stat(
    session_factory: Any, symbols_polled: int, latency_ms: float, errors: int
) -> None:
    try:
        with session_factory() as session:
            session.add(
                CollectorStat(
                    symbols_polled=symbols_polled,
                    latency_ms=latency_ms,
                    errors=errors,
                )
            )
            # Keep a small rolling diagnostic window (roughly one day at 1/min).
            cutoff = datetime.now(timezone.utc) - timedelta(days=2)
            session.execute(delete(CollectorStat).where(CollectorStat.timestamp < cutoff))
            session.commit()
    except Exception:  # pragma: no cover
        pass
