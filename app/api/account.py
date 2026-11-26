"""Account endpoint; all credentials and secrets are excluded."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..dependencies import AppContext, get_context, require_api_key
from . import ok

router = APIRouter(tags=["account"], dependencies=[Depends(require_api_key)])


@router.get(
    "/account",
    summary="Sanitized account and broker information",
    description="Never returns passwords, tokens or terminal configuration secrets.",
)
async def get_account(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    account = await asyncio.to_thread(ctx.account.get_account)
    return ok(account)
