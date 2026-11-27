"""API key authentication and safe request helpers."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from ..config import Settings


def is_valid_api_key(supplied: str | None, settings: Settings) -> bool:
    """Constant-time check against current/previous keys."""
    if not supplied:
        return False
    return any(hmac.compare_digest(supplied, key) for key in settings.api_keys)


def verify_api_key(request: Request, settings: Settings) -> bool:
    """Verify the configured REST header on an incoming request."""
    return is_valid_api_key(request.headers.get(settings.API_KEY_HEADER, ""), settings)


def client_identifier(request: Request, settings: Settings) -> str:
    """Stable, non-secret identifier for per-client limits."""
    supplied = request.headers.get(settings.API_KEY_HEADER, "")
    if supplied:
        return "key:" + hashlib.sha256(supplied.encode("utf-8")).hexdigest()[:16]
    host = request.client.host if request.client else "unknown"
    return "host:" + host


__all__ = ["client_identifier", "is_valid_api_key", "verify_api_key"]
