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


class PartRegistryCollisionError(DubISError):
    """A ledger row's part numbers map to two different registered parts."""


class DataDirLockedError(DubISError):
    """Another dubIS server process already holds the exclusive lock on this
    data directory (`<data_dir>/.dubis_lock`) — see server/lockfile.py.

    Carries the other process's pid/port (read from the lock file's
    content) so callers can build an actionable error message/dialog
    without re-reading the lock file themselves."""

    def __init__(self, message: str, *, pid: int | None = None,
                 port: int | None = None, data_dir: str = ""):
        super().__init__(message)
        self.pid = pid
        self.port = port
        self.data_dir = data_dir
