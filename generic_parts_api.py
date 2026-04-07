"""Generic Parts API — facade for generic part CRUD, matching, and spec extraction."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Callable

import generic_parts
import spec_extractor

logger = logging.getLogger(__name__)


class GenericPartsApi:
    """Thin facade over generic_parts + spec_extractor modules.

    Exposed to JS via InventoryApi delegation so the pywebview API surface
    stays identical.
    """

    def __init__(self, *, get_cache: Callable[[], sqlite3.Connection], events_dir: str):
        self._get_cache = get_cache
        self.events_dir = events_dir

    def _ensure_events_dir(self) -> None:
        """Create the events directory if it doesn't exist."""
        os.makedirs(self.events_dir, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────────

    def create_generic_part(self, name: str, part_type: str,
                            spec_json: str | dict, strictness_json: str | dict) -> dict[str, Any]:
        """Create a generic part with auto-matching."""
        spec = json.loads(spec_json) if isinstance(spec_json, str) else spec_json
        strictness = json.loads(strictness_json) if isinstance(strictness_json, str) else strictness_json
        conn = self._get_cache()
        self._ensure_events_dir()
        gp = generic_parts.create_generic_part(conn, self.events_dir, name, part_type, spec, strictness)
        gp["members"] = self._fetch_members(conn, gp["generic_part_id"])
        return gp

    def resolve_bom_spec(self, part_type: str, value: float,
                         package: str) -> dict[str, Any] | None:
        """Resolve a BOM spec to a generic part and its best real part."""
        conn = self._get_cache()
        return generic_parts.resolve_bom_spec(conn, part_type, float(value), package)

    def list_generic_parts(self) -> list[dict[str, Any]]:
        """List all generic parts with their members and extracted member specs."""
        conn = self._get_cache()
        return generic_parts.list_generic_parts_with_member_specs(conn)

    def add_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        """Add a real part to a generic group."""
        conn = self._get_cache()
        self._ensure_events_dir()
        generic_parts.add_member(conn, self.events_dir, generic_part_id, part_id)
        return self._fetch_members(conn, generic_part_id)

    def remove_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        """Remove a real part from a generic group."""
        conn = self._get_cache()
        self._ensure_events_dir()
        generic_parts.remove_member(conn, self.events_dir, generic_part_id, part_id)
        return self._fetch_members(conn, generic_part_id)

    def set_preferred_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        """Set a member as the preferred part in a generic group."""
        conn = self._get_cache()
        self._ensure_events_dir()
        generic_parts.set_preferred(conn, self.events_dir, generic_part_id, part_id)
        return self._fetch_members(conn, generic_part_id)

    def update_generic_part(self, generic_part_id: str, name: str,
                            spec_json: str | dict, strictness_json: str | dict) -> dict[str, Any]:
        """Update a generic part's spec and re-run auto-matching."""
        spec = json.loads(spec_json) if isinstance(spec_json, str) else spec_json
        strictness = json.loads(strictness_json) if isinstance(strictness_json, str) else strictness_json
        conn = self._get_cache()
        self._ensure_events_dir()
        conn.execute(
            "UPDATE generic_parts SET name=?, spec_json=?, strictness_json=? WHERE generic_part_id=?",
            (name, json.dumps(spec), json.dumps(strictness), generic_part_id),
        )
        # Re-run auto-matching: remove auto members, re-add
        conn.execute(
            "DELETE FROM generic_part_members WHERE generic_part_id=? AND source='auto'",
            (generic_part_id,),
        )
        conn.commit()
        generic_parts._auto_match(conn, self.events_dir, generic_part_id, spec, strictness)
        members = self._fetch_members(conn, generic_part_id)
        return {
            "generic_part_id": generic_part_id,
            "name": name,
            "part_type": conn.execute(
                "SELECT part_type FROM generic_parts WHERE generic_part_id=?",
                (generic_part_id,),
            ).fetchone()["part_type"],
            "spec": spec,
            "strictness": strictness,
            "members": members,
        }

    def extract_spec(self, part_key: str) -> dict[str, Any]:
        """Extract component spec from a part's description/metadata."""
        conn = self._get_cache()
        row = conn.execute(
            "SELECT description, package FROM parts WHERE part_id=?",
            (part_key,),
        ).fetchone()
        if not row:
            return {}
        return spec_extractor.extract_spec(row["description"] or "", row["package"] or "")

    # ── Private helpers ─────────────────────────────────────────────────────

    def _fetch_members(self, conn: sqlite3.Connection,
                       generic_part_id: str) -> list[dict[str, Any]]:
        """Fetch members for a generic part with stock quantities."""
        members = conn.execute(
            """SELECT gm.part_id, gm.source, gm.preferred, s.quantity
               FROM generic_part_members gm
               JOIN stock s USING (part_id)
               WHERE gm.generic_part_id = ?""",
            (generic_part_id,),
        ).fetchall()
        return [dict(m) for m in members]
