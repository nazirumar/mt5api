"""Multi-timeframe snapshot engine (Phase 19).

The engine coordinates existing MT5 services and Phase 18 analytics; it does
not open connections, fetch in parallel, or duplicate context calculations.
"""

from .engine import SnapshotEngine

__all__ = ["SnapshotEngine"]
