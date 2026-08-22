"""Exhaustiveness guard: every DubISError subclass has a *deliberate* HTTP
mapping in server/errors.py._MAPPING, or is explicitly opted into the generic
500 base fallback.

Without this, a new error type (say a future NotFoundError that should be 404)
would silently inherit the DubISError base -> 500. That's a wrong-status bug
that no existing test catches. This forces every new error class to make the
status decision, reviewable in the diff.
"""

from __future__ import annotations

import dubis_errors
from dubis_errors import DubISError
from server.errors import _MAPPING

# This guard walks DubISError.__subclasses__(), which only sees classes whose
# defining module has been imported — so it is only as exhaustive as this import
# list. Left implicit, it passed in isolation and failed in a full run (whichever
# other test happened to import reader_install first decided the verdict). Any
# module that defines a DubISError subclass outside dubis_errors.py belongs here.
import fleet_client  # noqa: F401,E402
import reader_install  # noqa: F401,E402
import reader_jobs  # noqa: F401,E402
import reader_runtime  # noqa: F401,E402

# Subclasses that intentionally fall through to the (DubISError, 500) base entry,
# each with the reason a specific status isn't warranted.
_READER_IS_CLIENT_SIDE = (
    "picture/PDF reader install runs on the CLIENT machine over the pywebview "
    "shell (there is no local /v1 in remote-backend mode) — never raised inside "
    "a /v1 request"
)
BASE_FALLBACK_OK = {
    "DataDirLockedError": "startup/lock failure — never raised inside a /v1 request",
    "ReaderInstallError": _READER_IS_CLIENT_SIDE,
    "DownloadError": _READER_IS_CLIENT_SIDE,
    "ChecksumMismatchError": _READER_IS_CLIENT_SIDE,
    "UnsafeUninstallTargetError": _READER_IS_CLIENT_SIDE,
    "ReaderRuntimeError": _READER_IS_CLIENT_SIDE,
    "ReaderPlatformUnsupportedError": _READER_IS_CLIENT_SIDE,
    "ReaderProcessExitedError": _READER_IS_CLIENT_SIDE,
    "ReaderStartTimeoutError": _READER_IS_CLIENT_SIDE,
    "ReaderJobError": _READER_IS_CLIENT_SIDE,
    "NoReaderTierError": _READER_IS_CLIENT_SIDE,
    "ReaderVerifyError": _READER_IS_CLIENT_SIDE,
}


def _all_subclasses(cls) -> set[type]:
    out = set()
    for sub in cls.__subclasses__():
        out.add(sub)
        out |= _all_subclasses(sub)
    return out


def test_every_dubis_error_is_deliberately_mapped():
    mapped = {exc_type for exc_type, _status, _code in _MAPPING}
    offenders = []
    for exc in _all_subclasses(DubISError):
        # Explicitly mapped (itself or via a mapped ancestor other than the base)?
        specific = any(
            issubclass(exc, m) and m is not DubISError for m in mapped
        )
        if specific:
            continue
        if exc.__name__ in BASE_FALLBACK_OK:
            continue
        offenders.append(exc.__name__)
    assert not offenders, (
        "DubISError subclass(es) have no deliberate HTTP status — they silently "
        "become 500 via the base fallback. Add an entry to server/errors._MAPPING, "
        "or justify the 500 in BASE_FALLBACK_OK:\n  " + "\n  ".join(sorted(offenders))
    )


def test_base_fallback_allowlist_has_no_stale_entries():
    names = {c.__name__ for c in _all_subclasses(DubISError)}
    stale = sorted(set(BASE_FALLBACK_OK) - names)
    assert not stale, f"BASE_FALLBACK_OK names errors that no longer exist: {stale}"


def test_mapping_lists_subclasses_before_bases():
    """_MAPPING is order-sensitive (first matching type wins) — a base listed
    before its subclass would shadow the subclass's status."""
    for i, (a, _s, _c) in enumerate(_MAPPING):
        for b, _s2, _c2 in _MAPPING[i + 1:]:
            assert not (issubclass(b, a) and b is not a), (
                f"{b.__name__} is a subclass of earlier entry {a.__name__} in "
                "_MAPPING — it will never match; move it before {a.__name__}."
            )


# Silence unused-import lint while keeping the module imported so its subclasses
# are registered for __subclasses__() discovery.
_ = dubis_errors
