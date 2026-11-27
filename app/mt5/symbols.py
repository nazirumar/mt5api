"""Broker symbol discovery, classification and exact-match lookup."""

from __future__ import annotations

from typing import Any

from ..core.exceptions import SymbolNotFoundError
from ..core.helpers import as_float, as_int, attr
from ..core.logging import get_logger
from .connection import MT5ConnectionManager

logger = get_logger("nazbeen_mt5.mt5.symbols")

MAJORS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"}
MINORS = {
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD", "GBPJPY", "GBPCHF",
    "GBPAUD", "GBPCAD", "GBPNZD", "AUDJPY", "AUDCHF", "AUDNZD", "CADJPY", "CHFJPY",
    "NZDJPY", "NZDCHF", "CADCHF",
}
METAL_TOKENS = ("XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER", "PLATINUM", "PALLADIUM")
INDEX_TOKENS = ("US30", "US500", "NAS", "SPX", "NDX", "DAX", "FTSE", "CAC", "NIKKEI", "HKG", "AUS200", "GER40", "UK100")
CRYPTO_TOKENS = ("BTC", "ETH", "LTC", "XRP", "SOL", "DOGE")


def normalize_symbol_name(symbol: str) -> str:
    """Strip broker suffixes only for classification - never for lookup."""
    name = symbol.upper()
    for separator in (".", "_", "#", "-", "/", "m"):
        if separator in name:
            name = name.split(separator, 1)[0]
    return "".join(ch for ch in name if ch.isalnum())


def classify_symbol(symbol: str, description: str = "") -> str:
    name = normalize_symbol_name(symbol)
    text = f"{name} {description}".upper()
    if any(token in text for token in METAL_TOKENS):
        return "metal"
    if any(token in text for token in CRYPTO_TOKENS):
        return "crypto"
    if any(token in text for token in INDEX_TOKENS):
        return "index"
    if "OIL" in text or "WTI" in text or "BRENT" in text or "NATURAL" in text:
        return "energy"
    if name in MAJORS:
        return "forex_major"
    if name in MINORS:
        return "forex_minor"
    if len(name) == 6 and name[:3].isalpha() and name[3:].isalpha():
        return "forex_exotic"
    return "other"


class SymbolService:
    """Read-only broker symbol catalog."""

    def __init__(self, manager: MT5ConnectionManager, settings: Any):
        self.manager = manager
        self.settings = settings

    def symbol_info(self, info: Any) -> dict[str, Any]:
        symbol = attr(info, "name", "") or ""
        description = attr(info, "description", "") or ""
        return {
            "symbol": symbol,
            "description": description,
            "category": classify_symbol(symbol, description),
            "base_currency": attr(info, "currency_base", "") or "",
            "quote_currency": attr(info, "currency_profit", "") or "",
            "digits": as_int(attr(info, "digits", 0)),
            "point": as_float(attr(info, "point", 0)),
            "tick_size": as_float(attr(info, "trade_tick_size", 0)),
            "tick_value": as_float(attr(info, "trade_tick_value", 0)),
            "contract_size": as_float(attr(info, "trade_contract_size", 0)),
            "trade_mode": as_int(attr(info, "trade_mode", 0)),
            "trade_allowed": bool(attr(info, "trade_allowed", False)),
            "visible": bool(attr(info, "visible", True)),
            "bid": as_float(attr(info, "bid", 0)),
            "ask": as_float(attr(info, "ask", 0)),
        }

    def list_symbols(self) -> list[dict[str, Any]]:
        infos = self.manager.call("symbols_get", allow_none=True)
        if infos is None:
            infos = []
        result = [self.symbol_info(info) for info in infos if attr(info, "name", None)]
        result.sort(key=lambda item: item["symbol"])
        return result

    def search_symbols(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        needle = (query or "").strip().lower()
        if not needle:
            return self.list_symbols()[:limit]
        matches = [
            item for item in self.list_symbols()
            if needle in item["symbol"].lower() or needle in item["description"].lower()
        ]
        return matches[:limit]

    def get_symbol(self, symbol: str) -> dict[str, Any]:
        info = self.manager.call("symbol_info", symbol, allow_none=True)
        if info is None:
            similar = self.search_symbols(symbol, limit=5)
            raise SymbolNotFoundError(
                f"Symbol '{symbol}' was not found on this broker",
                details={"suggestions": [item["symbol"] for item in similar]},
            )
        return self.symbol_info(info)
