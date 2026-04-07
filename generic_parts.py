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


def _record_event(events_dir: str, event_type: str,
                   part_id: str = "", generic_part_id: str = "",
                   data: dict | None = None) -> None:
    """Append an event to part_events.csv."""
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

    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "create_generic", generic_part_id=generic_part_id,
                   data={"name": name, "part_type": part_type, "spec": spec,
                          "strictness": strictness})

    return {"generic_part_id": generic_part_id, "name": name,
            "part_type": part_type, "spec": spec, "strictness": strictness}


def _auto_match(conn: Any, generic_part_id: str, part_type: str,
                 spec: dict, strictness: dict) -> None:
    """Find and insert auto-matched members for a generic part."""
    # Remove old auto-matches (keep manual)
    conn.execute(
        "DELETE FROM generic_part_members WHERE generic_part_id=? AND source='auto'",
        (generic_part_id,),
    )
    # Scan all parts and check spec match
    parts = conn.execute(
        "SELECT part_id, description, package, section FROM parts"
    ).fetchall()
    for part in parts:
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


def add_member(conn: Any, events_dir: str, generic_part_id: str,
                part_id: str, source: str = "manual") -> None:
    """Add a part to a generic group (manual override)."""
    conn.execute(
        """INSERT OR REPLACE INTO generic_part_members
           (generic_part_id, part_id, source, preferred)
           VALUES (?, ?, ?, 0)""",
        (generic_part_id, part_id, source),
    )
    conn.commit()
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "add_member", part_id=part_id,
                   generic_part_id=generic_part_id, data={"source": source})


def remove_member(conn: Any, events_dir: str, generic_part_id: str,
                   part_id: str) -> None:
    """Remove a part from a generic group."""
    conn.execute(
        "DELETE FROM generic_part_members WHERE generic_part_id=? AND part_id=?",
        (generic_part_id, part_id),
    )
    conn.commit()
    os.makedirs(events_dir, exist_ok=True)
    _record_event(events_dir, "remove_member", part_id=part_id,
                   generic_part_id=generic_part_id)


def set_preferred(conn: Any, events_dir: str, generic_part_id: str,
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
