"""Generic Parts API — facade for generic part CRUD, matching, and spec extraction."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Callable

import generic_parts
import saved_searches
import spec_extractor

logger = logging.getLogger(__name__)


class GenericPartsApi:
    """Thin facade over generic_parts + spec_extractor modules.

    Exposed to JS via InventoryApi delegation so the pywebview API surface
    stays identical.
    """

    def __init__(self, *, get_cache: Callable[[], sqlite3.Connection], events_dir: str,
                 data_dir: str | None = None):
        self._get_cache = get_cache
        self.events_dir = events_dir
        # data_dir is where saved_searches.json and other data files live.
        # If not provided, derive it from events_dir (events_dir == data_dir/events/).
        self._data_dir = data_dir if data_dir is not None else os.path.dirname(events_dir)

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

    def exclude_generic_member(self, generic_part_id: str, part_id: str) -> None:
        """Mark a member as excluded from a generic part group."""
        conn = self._get_cache()
        self._ensure_events_dir()
        generic_parts.exclude_member(conn, self.events_dir, generic_part_id, part_id)

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

    def preview_generic_members(self, spec_json: str | dict,
                                part_type: str,
                                strictness_json: str | dict) -> list[dict[str, Any]]:
        """Preview matching members without creating a group."""
        spec = json.loads(spec_json) if isinstance(spec_json, str) else spec_json
        strictness = json.loads(strictness_json) if isinstance(strictness_json, str) else strictness_json
        conn = self._get_cache()
        return generic_parts.preview_members(conn, part_type, spec, strictness)

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

    def extract_spec_from_value(self, part_type: str, value_str: str, package_str: str) -> dict[str, Any]:
        """Extract a spec dict from a raw BOM value string and package.

        Used when auto-creating a generic group from a BOM row that has no
        existing group (data-bom-value / data-bom-pkg attributes).

        Prepends the part_type as a keyword so that spec_extractor's type
        detection succeeds and value/voltage/tolerance fields are parsed.
        """
        # Prepend type keyword so extract_spec recognises the component type
        # and runs the correct value parser (capacitance / resistance / inductance).
        desc = part_type + " " + value_str + " " + package_str
        spec = spec_extractor.extract_spec(desc, package_str)
        # Always set type explicitly — extract_spec may still fall back to "other"
        # for unknown types, but the caller has the authoritative type.
        spec["type"] = part_type
        return spec

    def list_saved_searches(self, generic_part_id: str) -> list[dict[str, Any]]:
        """Return all saved searches for a generic part group."""
        conn = self._get_cache()
        return saved_searches.list_for_group(conn, generic_part_id)

    def create_saved_search(self, generic_part_id: str, name: str,
                            tag_state_json: str | dict, search_text: str,
                            frozen_members_json: str | list) -> dict[str, Any]:
        """Create a saved search and persist it to JSON."""
        tag_state = json.loads(tag_state_json) if isinstance(tag_state_json, str) else tag_state_json
        frozen_members = (
            json.loads(frozen_members_json)
            if isinstance(frozen_members_json, str)
            else frozen_members_json
        )
        conn = self._get_cache()
        return saved_searches.create(
            conn, self._data_dir, generic_part_id, name, tag_state, search_text, frozen_members
        )

    def delete_saved_search(self, search_id: str) -> None:
        """Delete a saved search by id and update JSON."""
        conn = self._get_cache()
        saved_searches.delete(conn, self._data_dir, search_id)

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
