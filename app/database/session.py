"""Database engine/session factory creation with automatic schema setup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base


def create_database(settings: Any) -> dict[str, Any]:
    path: Path = settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)
    return {"engine": engine, "session_factory": sessionmaker(bind=engine, expire_on_commit=False)}
