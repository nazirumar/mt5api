"""API key authentication and safe request helpers."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from ..config import Settings


def verify_api_key(request: Request, settings: Settings) -> bool:
    supplied = request.headers.get(settings.API_KEY_HEADER, "")
    expected = settings.API_KEY
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


def make_api_key_dependency(settings: Settings):
    async def require_api_key(
        api_key: Annotated[str | None, Header(alias=settings.API_KEY_HEADER)] = None,
    ) -> str:
        if not api_key or not hmac.compare_digest(api_key, settings.API_KEY):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "Valid API key required"},
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return api_key

    return require_api_key
