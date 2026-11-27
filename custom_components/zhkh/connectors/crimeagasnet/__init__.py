"""Крымгазсети connector."""

from .client import (
    CrimeaGasnetAuthError,
    CrimeaGasnetClient,
    CrimeaGasnetConnectionError,
)

__all__ = [
    "CrimeaGasnetAuthError",
    "CrimeaGasnetClient",
    "CrimeaGasnetConnectionError",
]
