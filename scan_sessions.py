"""Scan session registry — in-memory session store for the phone capture flow.

Sessions are keyed by a random URL-safe token (the QR-code / capture-page ``s``
query parameter). No background thread: expired sessions are pruned lazily on
every access. Created by inventory_api.start_scan_session().

Threading note: all four helpers mutate ``server._scan_sessions`` in place under
CPython's GIL — no explicit lock is needed, and none should be added.
"""

import logging
import secrets
import time

logger = logging.getLogger(__name__)

# Scan session time-to-live (seconds). A QR code / capture page is only useful
# for a short window; expire sessions so stale ids can't be replayed.
SCAN_SESSION_TTL = 15 * 60

# ── Scan session registry ──
#
# Stored on the server object as ``server._scan_sessions`` (a dict keyed by
# session id). No background thread: expired sessions are pruned lazily on every
# access. Created by inventory_api.start_scan_session().


def _scan_sessions(server):
    """Return (creating if needed) the server's scan-session registry."""
    registry = getattr(server, "_scan_sessions", None)
    if registry is None:
        registry = {}
        server._scan_sessions = registry
    return registry


def _prune_scan_sessions(server, now=None):
    """Drop sessions older than SCAN_SESSION_TTL. Returns the live registry."""
    now = time.time() if now is None else now
    registry = _scan_sessions(server)
    expired = [
        sid for sid, s in registry.items()
        if now - s.get("created", 0) > SCAN_SESSION_TTL
    ]
    for sid in expired:
        del registry[sid]
    return registry


def create_scan_session(server, template):
    """Register a new scan session on *server* and return its id.

    Used by inventory_api.start_scan_session(); kept here so the registry lives
    next to the routes that consume it.
    """
    registry = _prune_scan_sessions(server)
    session_id = secrets.token_urlsafe(16)
    registry[session_id] = {"template": template, "created": time.time()}
    return session_id


def _get_scan_session(server, session_id):
    """Return the live session dict for *session_id*, or None if missing/expired."""
    if not session_id:
        return None
    registry = _prune_scan_sessions(server)
    return registry.get(session_id)
