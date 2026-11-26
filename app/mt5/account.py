"""Sanitized account and broker information (no secrets exposed)."""

from __future__ import annotations

from typing import Any

from ..core.helpers import as_float, attr
from .connection import MT5ConnectionManager


class AccountService:
    SAFE_FIELDS = (
        "login",
        "server",
        "company",
        "name",
        "currency",
        "balance",
        "credit",
        "profit",
        "equity",
        "margin",
        "margin_free",
        "margin_level",
        "leverage",
        "trade_mode",
    )

    def __init__(self, manager: MT5ConnectionManager, settings: Any):
        self.manager = manager
        self.settings = settings

    def get_account(self) -> dict[str, Any]:
        info = self.manager.call("account_info", allow_none=True)
        if info is None:
            return {"connected": False, "account": None, "broker": self.manager.inspect().get("broker")}
        data: dict[str, Any] = {field: attr(info, field, None) for field in self.SAFE_FIELDS}
        for field in ("balance", "credit", "profit", "equity", "margin", "margin_free", "margin_level"):
            if data.get(field) is not None:
                data[field] = as_float(data[field])
        data["connected"] = True
        data["broker"] = self.manager.inspect().get("broker") or attr(info, "company", None)
        return data
