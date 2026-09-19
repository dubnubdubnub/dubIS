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


class SourceConfigError(DubISError):
    """A multi-server source registry operation was rejected as invalid.

    Bad URL (no http(s) scheme), duplicate id or URL, a reserved id, editing or
    removing the implicit `local` source, activating a disabled source, or
    asking to proxy a path that is local-only. Mapped to HTTP 400 by
    server/errors.py: every one of these is the caller describing a source
    wrongly, not a server fault — see
    docs/plans/2026-09-19-multi-server-hub-design.md.
    """


class SourceNotFoundError(NotFoundError):
    """No source with that id is registered on this hub.

    Subclasses NotFoundError deliberately, so it inherits that entry's 404 —
    asking for a source id that was never added (or has just been removed) is
    the same class of client error as asking for a missing cart.
    """


class SourceProtocolError(DubISError):
    """A source answered, but not with something dubIS can use.

    A captive portal, an SSO redirect, an nginx welcome page, a health-check
    shim, or a dubIS that renamed its response envelope: all of them are up,
    answer 200 and speak JSON. Mapped to HTTP 502 by server/errors.py, and —
    more importantly — recorded per source by `server/fanout.py` as a source
    that did NOT answer.

    This exists because the alternative is the worst failure this feature has:
    a merged total short by a whole machine is a *smaller perfectly plausible
    number*, with no shape to it, that the user's next ordering decision is made
    on. "Reachable" and "contributed" must never be allowed to disagree
    silently.
    """


class SourceUnavailableError(DubISError):
    """A remote source could not be reached, or answered unusably.

    Mapped to HTTP 502 by server/errors.py: the hub itself is fine, the
    upstream it was asked to speak to is not. NOTE this is raised only where a
    single source IS the answer (active = that source). A source being
    unreachable during a `merged` fan-out is expected and must DEGRADE the
    merged view (server/fanout.py records a per-source error entry), never fail
    it.
    """

    def __init__(self, message: str, *, source_id: str = "", url: str = "") -> None:
        super().__init__(message)
        self.source_id = source_id
        self.url = url
