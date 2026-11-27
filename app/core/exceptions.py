"""Domain exceptions mapped to consistent JSON error responses."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for expected API errors."""

    code = "SERVICE_ERROR"
    status_code = 500

    def __init__(self, message: str, *, code: str | None = None, details: object | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details


class MT5UnavailableError(ServiceError):
    code = "MT5_UNAVAILABLE"
    status_code = 503

    def __init__(self, message: str = "MetaTrader 5 is not available", details: object | None = None):
        super().__init__(message, details=details)


class MT5NotInitializedError(MT5UnavailableError):
    code = "MT5_NOT_INITIALIZED"
    status_code = 503


class MT5OperationError(ServiceError):
    code = "MT5_OPERATION_FAILED"
    status_code = 502


class SymbolNotFoundError(ServiceError):
    code = "SYMBOL_NOT_FOUND"
    status_code = 404


class InvalidTimeframeError(ServiceError):
    code = "INVALID_TIMEFRAME"
    status_code = 400


class InvalidRequestError(ServiceError):
    code = "INVALID_REQUEST"
    status_code = 400


class DataValidationError(ServiceError):
    code = "DATA_VALIDATION_FAILED"
    status_code = 502


class RateLimitError(ServiceError):
    code = "RATE_LIMITED"
    status_code = 429
