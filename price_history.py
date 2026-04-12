"""Price observation event log — append-only recording and cache population."""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

OBSERVATIONS_FILE = "price_observations.csv"
FIELDNAMES = ["timestamp", "part_id", "distributor", "unit_price", "currency",
              "source", "moq", "note"]


def record_observations(
    events_dir: str,
    observations: list[dict[str, Any]],
) -> None:
    """Append price observations to the event log CSV."""
    csv_path = os.path.join(events_dir, OBSERVATIONS_FILE)
    write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for obs in observations:
            writer.writerow({
                "timestamp": obs.get("timestamp", ts),
                "part_id": obs["part_id"],
                "distributor": obs.get("distributor", ""),
                "unit_price": obs.get("unit_price", ""),
                "currency": obs.get("currency", ""),
                "source": obs.get("source", ""),
                "moq": obs.get("moq", ""),
                "note": obs.get("note", ""),
            })


def read_observations(
    events_dir: str,
    part_id: str | None = None,
) -> list[dict[str, str]]:
    """Read price observations, optionally filtered by part_id."""
    csv_path = os.path.join(events_dir, OBSERVATIONS_FILE)
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if part_id:
        rows = [r for r in rows if r.get("part_id") == part_id]
    return rows


def _build_part_id_resolver(conn: Any) -> tuple[set[str], dict[str, str]]:
    """Build lookup structures for resolving distributor PNs to part_ids."""
    known: set[str] = set()
    dist_to_pid: dict[str, str] = {}
    try:
        for row in conn.execute(
            "SELECT part_id, lcsc, mpn, digikey, pololu, mouser FROM parts"
        ):
            pid = row["part_id"]
            known.add(pid)
            for col in ("lcsc", "mpn", "digikey", "pololu", "mouser"):
                val = (row[col] or "").strip()
                if val and val != pid:
                    dist_to_pid[val] = pid
    except Exception:
        pass  # parts table may not be populated yet
    return known, dist_to_pid


def populate_prices_cache(conn: Any, events_dir: str) -> None:
    """Rebuild the prices cache table from all price observations."""
    conn.execute("DELETE FROM prices")
    observations = read_observations(events_dir)

    known_pids, dist_to_pid = _build_part_id_resolver(conn)

    agg: dict[tuple[str, str], dict] = {}
    for obs in observations:
        pid = obs.get("part_id", "").strip()
        dist = obs.get("distributor", "").strip()
        if not pid or not dist:
            continue
        # Resolve distributor PN to inventory part_id
        if known_pids and pid not in known_pids:
            resolved = dist_to_pid.get(pid)
            if resolved:
                pid = resolved
            else:
                logger.warning("populate_prices_cache: skipping unknown part_id %r", pid)
                continue
        try:
            price = float(obs["unit_price"])
        except (ValueError, TypeError):
            continue
        key = (pid, dist)
        if key not in agg:
            agg[key] = {"prices": [], "last_observed": "", "source": "", "moq": None}
        agg[key]["prices"].append(price)
        agg[key]["last_observed"] = obs.get("timestamp", "")
        agg[key]["source"] = obs.get("source", "")
        moq = obs.get("moq", "")
        if moq:
            try:
                agg[key]["moq"] = int(moq)
            except (ValueError, TypeError):
                pass

    for (pid, dist), data in agg.items():
        prices = data["prices"]
        latest = prices[-1]
        avg = sum(prices) / len(prices)
        conn.execute(
            """INSERT OR REPLACE INTO prices
               (part_id, distributor, latest_unit_price, avg_unit_price,
                price_count, last_observed, moq, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, dist, latest, avg, len(prices),
             data["last_observed"], data["moq"], data["source"]),
        )
    conn.commit()


# ── API-level helpers (formerly in PriceApi) ─────────────────────────────


def resolve_part_key(conn: sqlite3.Connection, key: str) -> str | None:
    """Resolve a distributor-specific PN to the inventory part_id.

    Checks for a direct match first, then searches distributor columns
    (lcsc, mpn, digikey, pololu, mouser) in the parts table.
    """
    try:
        if conn.execute("SELECT 1 FROM parts WHERE part_id = ?", (key,)).fetchone():
            return key
        for col in ("lcsc", "mpn", "digikey", "pololu", "mouser"):
            row = conn.execute(
                f"SELECT part_id FROM parts WHERE {col} = ?", (key,)
            ).fetchone()
            if row:
                return row["part_id"]
    except (sqlite3.OperationalError, sqlite3.InterfaceError):
        # Connection may be busy from a concurrent populate_prices_cache
        logger.debug("resolve_part_key: cache busy, falling back to raw key")
        return key
    return None


def record_fetched_prices(
    conn: sqlite3.Connection,
    events_dir: str,
    part_key: str,
    distributor: str,
    price_tiers: list[dict[str, Any]],
) -> None:
    """Record prices fetched from a distributor API/scraper."""
    resolved_key = resolve_part_key(conn, part_key)
    if not resolved_key:
        logger.warning("record_fetched_prices: no inventory part for %r", part_key)
        return
    os.makedirs(events_dir, exist_ok=True)
    observations = []
    for tier in price_tiers:
        price = float(tier.get("price", 0))
        if price <= 0:
            continue
        observations.append({
            "part_id": resolved_key,
            "distributor": distributor,
            "unit_price": price,
            "source": "live_fetch",
            "moq": tier.get("qty", ""),
        })
    if observations:
        record_observations(events_dir, observations)
        populate_prices_cache(conn, events_dir)


def get_price_summary(
    conn: sqlite3.Connection,
    events_dir: str,
    part_key: str,
) -> dict[str, dict[str, Any]]:
    """Get aggregated pricing per distributor for a part."""
    resolved_key = resolve_part_key(conn, part_key) or part_key
    try:
        if not conn.execute("SELECT 1 FROM prices LIMIT 1").fetchone():
            if os.path.exists(events_dir):
                populate_prices_cache(conn, events_dir)
        rows = conn.execute(
            "SELECT * FROM prices WHERE part_id = ?", (resolved_key,)
        ).fetchall()
    except (sqlite3.OperationalError, sqlite3.InterfaceError):
        # Cache busy from concurrent record_fetched_prices rebuild
        logger.debug("get_price_summary: cache busy for %r", part_key)
        return {}
    result = {}
    for row in rows:
        result[row["distributor"]] = {
            "latest_unit_price": row["latest_unit_price"],
            "avg_unit_price": row["avg_unit_price"],
            "price_count": row["price_count"],
            "last_observed": row["last_observed"],
            "moq": row["moq"],
            "source": row["source"],
        }
    return result
