"""Generic parts -- CRUD, auto-matching, popularity scoring, BOM resolution."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any

import spec_extractor

PART_EVENTS_FILE = "part_events.csv"
PART_EVENTS_FIELDS = ["timestamp", "event_type", "part_id", "generic_part_id", "data_json"]

_JSON_FILE = "generic_parts.json"


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def _persist(conn: Any, data_dir: str) -> None:
    """Write all manual generic-part state from SQLite to the durable JSON file.

    Manual groups, manual/excluded memberships, and preferred flags are
    user-created state; SQLite is a deletable cache, so this file is their
    source of truth (same pattern as saved_searches.json).
    """
    import csv_io  # noqa: PLC0415

    groups = [
        {
            "generic_part_id": r["generic_part_id"],
            "name": r["name"],
            "part_type": r["part_type"],
            "spec": json.loads(r["spec_json"]),
            "strictness": json.loads(r["strictness_json"]),
        }
        for r in conn.execute(
            "SELECT * FROM generic_parts WHERE source='manual' ORDER BY generic_part_id"
        ).fetchall()
    ]
    members = [
        {"generic_part_id": r["generic_part_id"], "part_id": r["part_id"],
         "source": r["source"]}
        for r in conn.execute(
            "SELECT generic_part_id, part_id, source FROM generic_part_members "
            "WHERE source IN ('manual','excluded') ORDER BY generic_part_id, part_id"
        ).fetchall()
    ]
    preferred = [
        {"generic_part_id": r["generic_part_id"], "part_id": r["part_id"]}
        for r in conn.execute(
            "SELECT generic_part_id, part_id FROM generic_part_members "
            "WHERE preferred=1 ORDER BY generic_part_id, part_id"
        ).fetchall()
    ]

    # Retain JSON-only records for members whose part is not currently in
    # `parts` (warn-skipped at load time -- see load_into_db).  Without this,
    # the next unrelated mutation's _persist snapshot -- built purely from the
    # DB -- would silently erase them, breaking the "the part may return"
    # promise documented in load_into_db.
    known_parts = {r[0] for r in conn.execute("SELECT part_id FROM parts").fetchall()}
    existing_path = _json_path(data_dir)
    if os.path.exists(existing_path):
        try:
            with open(existing_path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = {}
        db_member_keys = {(m["generic_part_id"], m["part_id"]) for m in members}
        for m in existing.get("members", []):
            key = (m["generic_part_id"], m["part_id"])
            if m["part_id"] not in known_parts and key not in db_member_keys:
                members.append(m)
        db_preferred_keys = {(p["generic_part_id"], p["part_id"]) for p in preferred}
        for p in existing.get("preferred", []):
            key = (p["generic_part_id"], p["part_id"])
            if p["part_id"] not in known_parts and key not in db_preferred_keys:
                preferred.append(p)

    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps({"version": 1, "groups": groups, "members": members,
                    "preferred": preferred}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_into_db(conn: Any, data_dir: str) -> None:
    """Restore manual generic-part state from generic_parts.json into SQLite.

    Called during rebuild AFTER auto_generate_passive_groups.  Idempotent.
    If the JSON file is missing but SQLite already holds manual state
    (pre-overlay users), bootstraps the file from the DB instead.
    """
    import logging  # noqa: PLC0415
    logger = logging.getLogger(__name__)

    path = _json_path(data_dir)
    if not os.path.exists(path):
        has_manual = conn.execute(
            "SELECT 1 FROM generic_parts WHERE source='manual' LIMIT 1"
        ).fetchone()
        if has_manual:
            _persist(conn, data_dir)
        return

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if data.get("version") != 1:
        raise ValueError(f"Unsupported generic_parts.json version: {data.get('version')!r}")

    known_parts = {r["part_id"] for r in conn.execute("SELECT part_id FROM parts").fetchall()}

    for g in data.get("groups", []):
        conn.execute(
            """INSERT OR REPLACE INTO generic_parts
               (generic_part_id, name, part_type, spec_json, strictness_json, source)
               VALUES (?,?,?,?,?,'manual')""",
            (g["generic_part_id"], g["name"], g["part_type"],
             json.dumps(g["spec"]), json.dumps(g["strictness"])),
        )
        _auto_match(conn, g["generic_part_id"], g["part_type"], g["spec"], g["strictness"])

    for m in data.get("members", []):
        if m["part_id"] not in known_parts:
            # Part was deleted from the ledger after this membership was saved;
            # inserting would violate the FK.  Warn (visible), keep the record
            # in JSON (the part may return), skip the DB row.
            logger.warning("generic_parts.json member %s references unknown part %s — skipped",
                           m["generic_part_id"], m["part_id"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO generic_part_members
               (generic_part_id, part_id, source, preferred) VALUES (?,?,?,0)""",
            (m["generic_part_id"], m["part_id"], m["source"]),
        )

    for p in data.get("preferred", []):
        conn.execute(
            "UPDATE generic_part_members SET preferred=1 "
            "WHERE generic_part_id=? AND part_id=?",
            (p["generic_part_id"], p["part_id"]),
        )
    conn.commit()
    logger.info("Loaded %d manual generic groups from %s", len(data.get("groups", [])), path)


def _record_event(events_dir: str, event_type: str,
                   part_id: str = "", generic_part_id: str = "",
                   data: dict | None = None) -> None:
    """Append an event to part_events.csv (append-only AUDIT TRAIL).

    This log is never replayed — it is not event-sourcing.  Durable state
    lives in data/generic_parts.json (see load_into_db / docs/entity-store.md).
    """
    csv_path = os.path.join(events_dir, PART_EVENTS_FILE)
    write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PART_EVENTS_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "event_type": event_type,
            "part_id": part_id,
            "generic_part_id": generic_part_id,
            "data_json": json.dumps(data) if data else "",
        })


def create_generic_part(
    conn: Any,
    events_dir: str,
    data_dir: str,
    name: str,
    part_type: str,
    spec: dict,
    strictness: dict,
    source: str = "manual",
) -> dict[str, Any]:
    """Create a generic part and auto-match existing real parts."""
    generic_part_id = spec_extractor.generate_generic_id(part_type, spec)

    conn.execute(
        """INSERT OR REPLACE INTO generic_parts
           (generic_part_id, name, part_type, spec_json, strictness_json, source)
           VALUES (?,?,?,?,?,?)""",
        (generic_part_id, name, part_type, json.dumps(spec), json.dumps(strictness), source),
    )

    # Auto-match: find all real parts whose spec matches
    _auto_match(conn, generic_part_id, part_type, spec, strictness)

    conn.commit()
    _persist(conn, data_dir)

    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "create_generic", generic_part_id=generic_part_id,
                   data={"name": name, "part_type": part_type, "spec": spec,
                          "strictness": strictness})

    return {"generic_part_id": generic_part_id, "name": name,
            "part_type": part_type, "spec": spec, "strictness": strictness}


def _auto_match(conn: Any, generic_part_id: str, part_type: str,
                 spec: dict, strictness: dict) -> None:
    """Find and insert auto-matched members for a generic part."""
    # Remove old auto-matches (keep manual and excluded)
    conn.execute(
        "DELETE FROM generic_part_members WHERE generic_part_id=? AND source='auto'",
        (generic_part_id,),
    )
    # Get excluded part_ids to skip
    excluded = {r[0] for r in conn.execute(
        "SELECT part_id FROM generic_part_members "
        "WHERE generic_part_id = ? AND source = 'excluded'",
        (generic_part_id,),
    ).fetchall()}

    # Scan all parts and check spec match
    parts = conn.execute(
        "SELECT part_id, description, package, section FROM parts"
    ).fetchall()
    for part in parts:
        if part["part_id"] in excluded:
            continue
        part_spec = spec_extractor.extract_spec(
            description=part["description"], package=part["package"],
        )
        if part_spec["type"] != part_type:
            continue
        if spec_extractor.spec_matches(part_spec, spec, strictness):
            conn.execute(
                """INSERT OR IGNORE INTO generic_part_members
                   (generic_part_id, part_id, source, preferred)
                   VALUES (?, ?, 'auto', 0)""",
                (generic_part_id, part["part_id"]),
            )


def preview_members(conn: Any, part_type: str, spec: dict, strictness: dict) -> list[dict[str, Any]]:
    """Return parts matching spec without creating a generic part."""
    parts = conn.execute(
        "SELECT p.part_id, p.description, p.package, "
        "COALESCE(s.quantity, 0) AS quantity "
        "FROM parts p LEFT JOIN stock s ON p.part_id = s.part_id"
    ).fetchall()
    matches = []
    for row in parts:
        part_id = row["part_id"]
        desc = row["description"]
        pkg = row["package"]
        qty = row["quantity"]
        part_spec = spec_extractor.extract_spec(desc, pkg)
        if part_spec.get("type") != part_type:
            continue
        if spec_extractor.spec_matches(part_spec, spec, strictness):
            matches.append({
                "part_id": part_id,
                "description": desc,
                "package": pkg,
                "quantity": qty,
                "spec": part_spec,
            })
    return matches


def add_member(conn: Any, events_dir: str, data_dir: str, generic_part_id: str,
                part_id: str, source: str = "manual") -> None:
    """Add a part to a generic group (manual override)."""
    conn.execute(
        """INSERT OR REPLACE INTO generic_part_members
           (generic_part_id, part_id, source, preferred)
           VALUES (?, ?, ?, 0)""",
        (generic_part_id, part_id, source),
    )
    conn.commit()
    _persist(conn, data_dir)
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "add_member", part_id=part_id,
                   generic_part_id=generic_part_id, data={"source": source})


def remove_member(conn: Any, events_dir: str, data_dir: str, generic_part_id: str,
                   part_id: str) -> None:
    """Remove a part from a generic group."""
    conn.execute(
        "DELETE FROM generic_part_members WHERE generic_part_id=? AND part_id=?",
        (generic_part_id, part_id),
    )
    conn.commit()
    _persist(conn, data_dir)
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "remove_member", part_id=part_id,
                   generic_part_id=generic_part_id)


def group_memberships_for_part(conn: Any, part_key: str) -> list[tuple[str, str]]:
    """(generic_part_id, name) pairs for every group this part currently belongs to."""
    rows = conn.execute(
        """SELECT gm.generic_part_id, gp.name FROM generic_part_members gm
           JOIN generic_parts gp ON gp.generic_part_id = gm.generic_part_id
           WHERE gm.part_id = ?
           ORDER BY gp.name""",
        (part_key,),
    ).fetchall()
    return [(r["generic_part_id"], r["name"]) for r in rows]


def get_group_names_for_part(conn: Any, part_key: str) -> list[str]:
    """Generic-part group names this part is currently a member of."""
    return [name for _, name in group_memberships_for_part(conn, part_key)]


def exclude_member(conn: Any, events_dir: str, data_dir: str, generic_part_id: str,
                    part_id: str) -> None:
    """Mark a member as excluded — survives auto-regeneration."""
    conn.execute(
        "INSERT OR REPLACE INTO generic_part_members "
        "(generic_part_id, part_id, source, preferred) VALUES (?, ?, 'excluded', 0)",
        (generic_part_id, part_id),
    )
    conn.commit()
    _persist(conn, data_dir)
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "exclude_member", part_id=part_id,
                   generic_part_id=generic_part_id)


def set_preferred(conn: Any, events_dir: str, data_dir: str, generic_part_id: str,
                   part_id: str) -> None:
    """Mark a part as preferred within its generic group."""
    # Clear existing preferred in this group
    conn.execute(
        "UPDATE generic_part_members SET preferred=0 WHERE generic_part_id=?",
        (generic_part_id,),
    )
    conn.execute(
        "UPDATE generic_part_members SET preferred=1 WHERE generic_part_id=? AND part_id=?",
        (generic_part_id, part_id),
    )
    conn.commit()
    _persist(conn, data_dir)
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "set_preferred", part_id=part_id,
                   generic_part_id=generic_part_id)


PASSIVE_SECTIONS = {"Passives - Capacitors", "Passives - Resistors", "Passives - Inductors"}


def auto_generate_passive_groups(conn: Any, events_dir: str) -> list[dict[str, Any]]:
    """Scan all parts in passive sections and create auto-generated generic groups.

    Groups parts by (type, value, package).  Deletes any previously auto-generated
    groups first (idempotent).  Never touches manually-created groups.

    Returns list of created group dicts.
    """
    # Remove all previously auto-generated groups and their members
    auto_ids = [
        row["generic_part_id"]
        for row in conn.execute(
            "SELECT generic_part_id FROM generic_parts WHERE source='auto'"
        ).fetchall()
    ]
    if auto_ids:
        placeholders = ",".join("?" * len(auto_ids))
        conn.execute(
            f"DELETE FROM generic_part_members WHERE generic_part_id IN ({placeholders})",
            auto_ids,
        )
        conn.execute(
            f"DELETE FROM generic_parts WHERE generic_part_id IN ({placeholders})",
            auto_ids,
        )

    # Scan passive parts and group by (type, value_display, package)
    parts = conn.execute(
        "SELECT part_id, description, package, section FROM parts"
    ).fetchall()

    # groups_map: generic_part_id -> {"name", "part_type", "spec", "member_ids"}
    groups_map: dict[str, dict[str, Any]] = {}

    for part in parts:
        if part["section"] not in PASSIVE_SECTIONS:
            continue
        part_spec = spec_extractor.extract_spec(
            description=part["description"], package=part["package"]
        )
        part_type = part_spec.get("type", "other")
        if part_type not in ("capacitor", "resistor", "inductor"):
            continue
        value_display = part_spec.get("value_display")
        if not value_display:
            continue
        package = (part_spec.get("package") or "").strip()

        # Build spec dict for the group (value as display string, package)
        group_spec = {"value": value_display, "package": package}
        generic_part_id = spec_extractor.generate_generic_id(part_type, group_spec)

        if generic_part_id not in groups_map:
            name = f"{value_display} {package}".strip() if package else value_display
            groups_map[generic_part_id] = {
                "generic_part_id": generic_part_id,
                "name": name,
                "part_type": part_type,
                "spec": group_spec,
                "member_ids": [],
            }
        groups_map[generic_part_id]["member_ids"].append(part["part_id"])

    # Persist groups and members
    created: list[dict[str, Any]] = []
    for gid, group in groups_map.items():
        conn.execute(
            """INSERT INTO generic_parts
               (generic_part_id, name, part_type, spec_json, strictness_json, source)
               VALUES (?, ?, ?, ?, ?, 'auto')""",
            (
                gid,
                group["name"],
                group["part_type"],
                json.dumps(group["spec"]),
                json.dumps({"required": ["value", "package"]}),
            ),
        )
        for part_id in group["member_ids"]:
            conn.execute(
                """INSERT OR IGNORE INTO generic_part_members
                   (generic_part_id, part_id, source, preferred)
                   VALUES (?, ?, 'auto', 0)""",
                (gid, part_id),
            )
        created.append({
            "generic_part_id": gid,
            "name": group["name"],
            "part_type": group["part_type"],
            "spec": group["spec"],
        })

    conn.commit()

    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "auto_generate_passive_groups",
                   data={"count": len(created)})

    return created


def list_generic_parts_with_member_specs(conn: Any) -> list[dict[str, Any]]:
    """List all generic parts with their members, each member including extracted spec fields.

    Returns a list of dicts with keys:
        generic_part_id, name, part_type, spec, strictness, source, members

    Each member dict has keys:
        part_id, source, preferred, quantity, description, package, spec
    """
    gps = conn.execute("SELECT * FROM generic_parts").fetchall()
    result = []
    for gp in gps:
        members_rows = conn.execute(
            """SELECT gm.part_id, gm.source, gm.preferred, s.quantity,
                      p.description, p.package
               FROM generic_part_members gm
               JOIN stock s USING (part_id)
               JOIN parts p USING (part_id)
               WHERE gm.generic_part_id = ?""",
            (gp["generic_part_id"],),
        ).fetchall()
        members = []
        for m in members_rows:
            member_spec = spec_extractor.extract_spec(
                description=m["description"],
                package=m["package"],
            )
            members.append({
                "part_id": m["part_id"],
                "source": m["source"],
                "preferred": m["preferred"],
                "quantity": m["quantity"],
                "description": m["description"],
                "package": m["package"],
                "spec": member_spec,
            })
        result.append({
            "generic_part_id": gp["generic_part_id"],
            "name": gp["name"],
            "part_type": gp["part_type"],
            "spec": json.loads(gp["spec_json"]),
            "strictness": json.loads(gp["strictness_json"]),
            "source": gp["source"],
            "members": members,
        })
    return result


def fetch_members(
    conn: Any,
    generic_part_id: str,
) -> list[dict[str, Any]]:
    """Fetch members for a generic part with stock quantities."""
    members = conn.execute(
        """SELECT gm.part_id, gm.source, gm.preferred, s.quantity
           FROM generic_part_members gm
           JOIN stock s USING (part_id)
           WHERE gm.generic_part_id = ?""",
        (generic_part_id,),
    ).fetchall()
    return [dict(m) for m in members]


def resolve_bom_spec(
    conn: Any,
    part_type: str,
    value: float,
    package: str,
) -> dict[str, Any] | None:
    """Find a generic part matching a BOM spec, return its best real part.

    Returns: {"generic_part_id", "generic_name", "best_part_id", "members"} or None.
    """
    generics = conn.execute("SELECT * FROM generic_parts").fetchall()
    for gp in generics:
        if gp["part_type"] != part_type:
            continue
        spec = json.loads(gp["spec_json"])
        strictness = json.loads(gp["strictness_json"])
        # Build a pseudo part_spec from the BOM values
        part_spec = {"type": part_type, "value": value, "package": package}
        if spec_extractor.spec_matches(part_spec, spec, strictness):
            # Found matching generic -- resolve to best member
            members = conn.execute(
                """SELECT gm.part_id, gm.preferred, s.quantity
                   FROM generic_part_members gm
                   JOIN stock s USING (part_id)
                   WHERE gm.generic_part_id = ?
                   ORDER BY gm.preferred DESC, s.quantity DESC""",
                (gp["generic_part_id"],),
            ).fetchall()
            if not members:
                continue
            return {
                "generic_part_id": gp["generic_part_id"],
                "generic_name": gp["name"],
                "best_part_id": members[0]["part_id"],
                "members": [{"part_id": m["part_id"], "preferred": m["preferred"],
                             "quantity": m["quantity"]} for m in members],
            }
    return None


# ── API-level helpers (formerly in GenericPartsApi) ──────────────────────


def _parse_json(value: str | dict) -> dict:
    """Parse a JSON string or pass through a dict."""
    return json.loads(value) if isinstance(value, str) else value


def create_generic_part_api(
    conn: Any,
    events_dir: str,
    data_dir: str,
    name: str,
    part_type: str,
    spec_json: str | dict,
    strictness_json: str | dict,
) -> dict[str, Any]:
    """Create a generic part with auto-matching. Parses JSON, returns with members."""
    spec = _parse_json(spec_json)
    strictness = _parse_json(strictness_json)
    os.makedirs(events_dir, exist_ok=True)
    gp = create_generic_part(conn, events_dir, data_dir, name, part_type, spec, strictness)
    gp["members"] = fetch_members(conn, gp["generic_part_id"])
    return gp


def update_generic_part_api(
    conn: Any,
    events_dir: str,
    data_dir: str,
    generic_part_id: str,
    name: str,
    spec_json: str | dict,
    strictness_json: str | dict,
) -> dict[str, Any]:
    """Update a generic part's spec and re-run auto-matching."""
    spec = _parse_json(spec_json)
    strictness = _parse_json(strictness_json)
    os.makedirs(events_dir, exist_ok=True)
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
    part_type = conn.execute(
        "SELECT part_type FROM generic_parts WHERE generic_part_id=?",
        (generic_part_id,),
    ).fetchone()["part_type"]
    _auto_match(conn, generic_part_id, part_type, spec, strictness)
    conn.commit()
    _persist(conn, data_dir)
    members = fetch_members(conn, generic_part_id)
    return {
        "generic_part_id": generic_part_id,
        "name": name,
        "part_type": part_type,
        "spec": spec,
        "strictness": strictness,
        "members": members,
    }


def add_member_api(
    conn: Any,
    events_dir: str,
    data_dir: str,
    generic_part_id: str,
    part_id: str,
) -> list[dict[str, Any]]:
    """Add a real part to a generic group and return updated members."""
    os.makedirs(events_dir, exist_ok=True)
    add_member(conn, events_dir, data_dir, generic_part_id, part_id)
    return fetch_members(conn, generic_part_id)


def remove_member_api(
    conn: Any,
    events_dir: str,
    data_dir: str,
    generic_part_id: str,
    part_id: str,
) -> list[dict[str, Any]]:
    """Remove a real part from a generic group and return updated members."""
    os.makedirs(events_dir, exist_ok=True)
    remove_member(conn, events_dir, data_dir, generic_part_id, part_id)
    return fetch_members(conn, generic_part_id)


def set_preferred_api(
    conn: Any,
    events_dir: str,
    data_dir: str,
    generic_part_id: str,
    part_id: str,
) -> list[dict[str, Any]]:
    """Set a member as preferred and return updated members."""
    os.makedirs(events_dir, exist_ok=True)
    set_preferred(conn, events_dir, data_dir, generic_part_id, part_id)
    return fetch_members(conn, generic_part_id)


def extract_spec_for_part(conn: Any, part_key: str) -> dict[str, Any]:
    """Extract component spec from a part's description/metadata in the cache."""
    row = conn.execute(
        "SELECT description, package FROM parts WHERE part_id=?",
        (part_key,),
    ).fetchone()
    if not row:
        return {}
    return spec_extractor.extract_spec(row["description"] or "", row["package"] or "")
