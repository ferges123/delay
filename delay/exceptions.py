"""Typed application and API exception hierarchy."""
from __future__ import annotations


class AppError(Exception):
    exit_code: int = 1


class ConfigError(AppError):
    exit_code: int = 2


class ApiError(AppError):
    exit_code: int = 3


class AuthError(ApiError):
    exit_code: int = 4


class RateLimitError(ApiError):
    exit_code: int = 5


class ServerError(ApiError):
    exit_code: int = 6


class NetworkTimeoutError(ApiError):
    exit_code: int = 7


class NetworkConnectionError(ApiError):
    exit_code: int = 8


class ValidationError(AppError):
    exit_code: int = 9
