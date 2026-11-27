#!/usr/bin/env python3
"""Standalone read-only client for the Nazbeen MT5 Market Data API.

Install one of:
    python -m pip install httpx
    python -m pip install requests

Environment:
    API_BASE_URL   Default: http://mt5.nazbeen.test
    API_KEY    Required (MT5_API_KEY accepted as a compatibility fallback)

This client only calls read endpoints. It never places, modifies, or closes trades.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Iterable
from urllib.parse import quote as url_quote

try:  # Prefer httpx.
    import httpx as _http

    _USING_HTTPX = True
except ImportError:  # Fall back to requests.
    try:
        import requests as _http  # type: ignore[no-redef]

        _USING_HTTPX = False
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Missing HTTP library. Install one with:\n"
            "  python -m pip install httpx\n"
            "or:\n"
            "  python -m pip install requests"
        ) from exc


DEFAULT_BASE_URL = "http://mt5.nazbeen.test"


class MT5ClientError(Exception):
    """Base error for this client."""


class ConfigurationError(MT5ClientError):
    """Missing or invalid local configuration."""


class MT5ConnectionError(MT5ClientError):
    """The API could not be reached."""


class APIError(MT5ClientError):
    """The API returned an error envelope or HTTP error."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code

    def __str__(self) -> str:
        base = f"[{self.code}] {self.message}"
        if self.status_code is not None:
            base = f"{base} (HTTP {self.status_code})"
        if self.details is not None:
            base = f"{base} details={self.details!r}"
        return base


class MT5MarketDataClient:
    """Read-only REST client for the MT5 Market Data API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("API_KEY") or os.getenv("MT5_API_KEY")
        self.timeout = timeout

        if not self.api_key:
            raise ConfigurationError(
                "API_KEY is not set. Configure it in your shell or .env, for example:\n"
                "  $env:API_KEY='your-long-random-secret'\n"
                "or add API_KEY=... to the project .env file. "
                "MT5_API_KEY is accepted as a compatibility fallback."
            )

    def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if auth:
            headers["X-API-Key"] = self.api_key  # type: ignore[assignment]

        status_code: int | None = None
        try:
            if _USING_HTTPX:
                with _http.Client(timeout=self.timeout) as client:
                    response = client.get(url, headers=headers, params=params)
                    status_code = response.status_code
                    payload = response.json()
            else:
                response = _http.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.timeout,
                )
                status_code = response.status_code
                payload = response.json()
        except Exception as exc:
            if self._looks_like_connection_error(exc):
                raise MT5ConnectionError(
                    f"Cannot reach {self.base_url}. Start the API and proxy tasks, then check "
                    f"{self.base_url}/api/v1/health. Original error: {exc}"
                ) from exc
            raise MT5ClientError(f"Request to {url} failed: {exc}") from exc

        if not isinstance(payload, dict):
            raise MT5ClientError(
                f"Unexpected response from {url}: expected a JSON object, got "
                f"{type(payload).__name__}."
            )

        if payload.get("success") is False:
            error = payload.get("error") or {}
            raise APIError(
                code=str(error.get("code", "UNKNOWN_ERROR")),
                message=str(error.get("message", "API request failed")),
                details=error.get("details"),
                status_code=status_code,
            )

        if "success" in payload:
            return payload

        # Tolerate a plain JSON response while keeping the documented envelope.
        return {"success": True, "data": payload, "meta": {}}

    @staticmethod
    def _looks_like_connection_error(exc: Exception) -> bool:
        name = type(exc).__name__
        return name in {
            "ConnectError",
            "ConnectTimeout",
            "ConnectionError",
            "Timeout",
            "TimeoutException",
            "ReadTimeout",
            "ChunkedEncodingError",
            "MaxRetryError",
        }

    @staticmethod
    def _data(envelope: dict[str, Any]) -> Any:
        return envelope.get("data")

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str] | str) -> list[str]:
        if isinstance(symbols, str):
            values = [symbols]
        else:
            values = list(symbols)
        cleaned = [str(symbol).strip() for symbol in values if str(symbol).strip()]
        if not cleaned:
            raise ValueError("At least one symbol is required.")
        return cleaned

    def health(self) -> Any:
        """Public health endpoint."""
        return self._data(self._request("/api/v1/health", auth=False))

    def symbols(self) -> Any:
        """List available MT5 symbols."""
        return self._data(self._request("/api/v1/symbols"))

    def quote(self, symbol: str) -> Any:
        """Get the latest quote for one symbol."""
        safe = url_quote(symbol, safe="")
        return self._data(self._request(f"/api/v1/quote/{safe}"))

    def quotes(self, symbols: Iterable[str] | str) -> Any:
        """Get quotes for several symbols in one request."""
        values = self._normalize_symbols(symbols)
        return self._data(
            self._request("/api/v1/quotes", params={"symbols": ",".join(values)})
        )

    def candles(
        self,
        symbol: str,
        timeframe: str = "M1",
        limit: int = 500,
        start: str | None = None,
        end: str | None = None,
    ) -> Any:
        """Get OHLC candles. Timeframes use MT5 tokens such as M1, M5, H1, MN1."""
        params: dict[str, Any] = {"timeframe": timeframe, "limit": limit}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        safe = url_quote(symbol, safe="")
        return self._data(self._request(f"/api/v1/candles/{safe}", params=params))

    def ticks(
        self,
        symbol: str,
        start: str,
        *,
        end: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """Get tick history from a UTC start timestamp."""
        params: dict[str, Any] = {"start": start}
        if end is not None:
            params["end"] = end
        if limit is not None:
            params["limit"] = limit
        safe = url_quote(symbol, safe="")
        return self._data(self._request(f"/api/v1/ticks/{safe}", params=params))

    def account(self) -> Any:
        """Get read-only broker account information."""
        return self._data(self._request("/api/v1/account"))


def _items(data: Any) -> list[Any]:
    """Extract a list from common response shapes without assuming field names."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("candles", "quotes", "symbols", "ticks", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _print_json(label: str, value: Any) -> None:
    import json

    print(f"{label}:")
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def main() -> int:
    try:
        client = MT5MarketDataClient()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        _print_json("Health", client.health())

        quote = client.quote("XAUUSD")
        _print_json("XAUUSD quote", quote)

        for timeframe in ("M1", "M5", "H1"):
            data = client.candles("XAUUSD", timeframe=timeframe, limit=10)
            candles = _items(data)
            print(f"\n{timeframe} candles: {len(candles)} returned")
            for candle in candles[:3]:
                print(candle)

        return 0

    except MT5ConnectionError as exc:
        print(f"Connection error: {exc}", file=sys.stderr)
        return 3
    except APIError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 4
    except MT5ClientError as exc:
        print(f"Client error: {exc}", file=sys.stderr)
        return 5
    except ValueError as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
