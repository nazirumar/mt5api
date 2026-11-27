"""Per-request context values shared by middleware, logs and responses."""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the current request ID, or an empty string outside a request."""
    return request_id_var.get()


def new_request_id() -> str:
    """Create a compact, opaque request identifier."""
    import uuid

    return uuid.uuid4().hex
