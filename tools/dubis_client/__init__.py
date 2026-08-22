"""Shared HTTP client + curation helpers for headless dubIS clients.

Split out of the retired tools/dubis-mcp so the CLI (tools/dubis-cli) and any
future headless caller share one implementation of discovery, auth, and the
compact projections. This package never touches the CSVs or the SQLite cache
directly — the /v1 server is the single writer.
"""

from .curate import (
    PartNotFoundError,
    compact_part,
    derive_part_key,
    fetch_inventory,
    find_part,
    matches_part,
    precheck_adjust,
    resolve_canonical_key,
)
from .v1client import NoServerFoundError, V1Client, V1Error, connect

__all__ = [
    "NoServerFoundError",
    "PartNotFoundError",
    "V1Client",
    "V1Error",
    "compact_part",
    "connect",
    "derive_part_key",
    "fetch_inventory",
    "find_part",
    "matches_part",
    "precheck_adjust",
    "resolve_canonical_key",
]
