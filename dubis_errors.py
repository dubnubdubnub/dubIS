"""dubIS exception hierarchy."""

from __future__ import annotations


class DubISError(Exception):
    """Base exception for all dubIS errors."""


class DistributorError(DubISError):
    """Error from a distributor client."""

    def __init__(self, message: str, *, provider: str = "", **kwargs):
        super().__init__(message)
        self.provider = provider
        for k, v in kwargs.items():
            setattr(self, k, v)


class DistributorTimeout(DistributorError):
    """Distributor request timed out."""

    def __init__(self, message: str, *, provider: str = "", part_number: str = ""):
        super().__init__(message, provider=provider)
        self.part_number = part_number


class DistributorAuthError(DistributorError):
    """Distributor authentication/session error."""


class CacheError(DubISError):
    """Error in cache database operations."""
