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


class NotFoundError(DubISError):
    """A requested entity (e.g. a cart) does not exist. Mapped to HTTP 404 by
    server/errors.py — distinct from the base DubISError (500), since a
    caller asking for a missing id is a client error, not a server fault."""


class AlternateRejectedError(DubISError):
    """A generic-part membership review would overwrite a recorded rejection.

    Raised by `domain.generic_parts.review_member` when a part previously
    rejected as an alternate for a group is proposed/approved again without
    `acknowledge_rejection=True`. Mapped to HTTP 409 by server/errors.py: the
    prior verdict is a conflict the caller must see, not a server fault — the
    point of storing a rejection is that the same bad idea cannot be
    re-proposed silently. Carries the prior review record so callers can show
    the original reason."""

    def __init__(self, message: str, *, generic_part_id: str = "",
                 part_id: str = "", review: dict | None = None):
        super().__init__(message)
        self.generic_part_id = generic_part_id
        self.part_id = part_id
        self.review = review or {}


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
