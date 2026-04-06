"""Price observation event log — append-only recording and cache population."""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

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


def populate_prices_cache(conn: Any, events_dir: str) -> None:
    """Rebuild the prices cache table from all price observations."""
    conn.execute("DELETE FROM prices")
    observations = read_observations(events_dir)

    agg: dict[tuple[str, str], dict] = {}
    for obs in observations:
        pid = obs.get("part_id", "").strip()
        dist = obs.get("distributor", "").strip()
        if not pid or not dist:
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
