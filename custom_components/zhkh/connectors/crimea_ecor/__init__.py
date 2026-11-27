"""Крымэкоресурсы connector."""

from .client import (
    CrimeaEcorAuthError,
    CrimeaEcorClient,
    CrimeaEcorConnectionError,
)

__all__ = [
    "CrimeaEcorAuthError",
    "CrimeaEcorClient",
    "CrimeaEcorConnectionError",
]
