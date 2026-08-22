"""Pricing domain — price/quantity parsing and price observation history."""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

import csv_io
from domain.packaging import carrier_of, clean_reel_fee, clean_reel_qty, is_reel

logger = logging.getLogger(__name__)

# ── price_ops ────────────────────────────────────────────────────────────────


def parse_qty(value: Any, default: int = 0) -> int:
    """Parse a quantity string to int, tolerating commas and floats."""
    try:
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return default


def parse_price(value: Any, default: float = 0.0) -> float:
    """Parse a price string to float, tolerating commas and dollar signs."""
    try:
        return float(str(value).replace(",", "").replace("$", "") or "0")
    except (ValueError, TypeError):
        return default


def ensure_parsed(value: str | Any) -> Any:
    """Parse JSON string if needed, otherwise return as-is."""
    return json.loads(value) if isinstance(value, str) else value


def derive_missing_price(
    unit_price: float | None,
    ext_price: float | None,
    qty: int,
) -> tuple[float | None, float | None]:
    """Fill in whichever of unit/ext is missing given the other + qty.

    Returns (unit_price, ext_price) with the missing value derived,
    or unchanged if both are provided, both are None, or qty is 0.
    """
    if unit_price is not None and unit_price != 0 and ext_price is None and qty > 0:
        ext_price = unit_price * qty
    elif ext_price is not None and ext_price != 0 and unit_price is None and qty > 0:
        unit_price = ext_price / qty
    return unit_price, ext_price


# ── price_history ─────────────────────────────────────────────────────────────

OBSERVATIONS_FILE = "price_observations.csv"

# The five packaging columns appended in
# `feat(pricing): persist packaging on price observations`. They come last so
# the migration is a pure append and the pre-existing column order is
# untouched. Every one of them is "" for an observation written before the
# migration, and "" means UNKNOWN, never a real value -- in particular an
# empty `carrier` is not "bulk" and an empty `is_reel` is not "not a reel".
#
#   packaging  the distributor's own name for this ladder's packaging, verbatim
#              ("Cut Tape (CT)", "Tape & Reel (TR)", "Digi-Reel", "Reel",
#              "Tray"). Kept raw because it is the provenance: carrier/is_reel
#              are derived from it, and re-deriving later (better tokens, a new
#              distributor's prose) needs the original string.
#   carrier    the normalized carrier from `domain.packaging.carrier_of`:
#              tape / tray / tube / bulk, or "" when the name is unrecognised.
#   is_reel    "1"/"0" from `domain.packaging.is_reel`, "" when unknown. Not
#              redundant with `carrier`: cut tape and tape & reel are both
#              carrier "tape" but different price ladders, so carrier alone
#              cannot keep them apart -- which is the whole point of the
#              change. A distributor with authoritative knowledge (LCSC
#              publishes an `isReel` flag that disagrees with its own unit
#              name) can pass it explicitly and it wins over the derived value.
#   reel_qty   the factory reel / packet quantity, the multiple a whole reel is
#              sold in (LCSC minPacketNumber). "" when not published; never 0,
#              which would read as "reels of zero parts".
#   reel_fee   the custom-reeling surcharge (LCSC reelPrice, Digi-Reel,
#              MouseReel). "" when the distributor does not offer or publish
#              one. Needed to price a reel option: the ladder alone understates
#              a custom-reeled buy by exactly this amount.
PACKAGING_FIELDNAMES = ["packaging", "carrier", "is_reel", "reel_qty", "reel_fee"]

FIELDNAMES = ["timestamp", "part_id", "distributor", "unit_price", "currency",
              "source", "moq", "note", *PACKAGING_FIELDNAMES]


def _clean_is_reel(value: Any) -> bool | None:
    """Normalize a reel flag, accepting the CSV's own wire format.

    `"0"` has to mean False. It is what this function *writes*, it is what
    `cart_qty._row_is_reel` reads back as False, and a non-empty string is
    truthy in Python -- so taking the raw value would flip every cut-tape row
    to a reel the moment an observation read from the file was written back.
    Silent and inverted is the worst pair of properties a bug can have here: a
    reel ladder's lowest break IS the reel quantity, so a mislabelled row can
    answer a 200-piece shortfall with a 5,000-piece reel.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "")
    return bool(value)


def _fmt_reel_qty(value: Any) -> str:
    qty = clean_reel_qty(value)
    return "" if qty is None else str(qty)


def _fmt_reel_fee(value: Any) -> str:
    fee = clean_reel_fee(value)
    return "" if fee is None else str(fee)


def _packaging_columns(obs: dict[str, Any]) -> dict[str, str]:
    """Render an observation's packaging metadata into its five CSV columns.

    `carrier` and `is_reel` are derived from the packaging name when the caller
    did not supply them (mirroring `domain.product.annotate_packagings`'s
    setdefault semantics, so a distributor that knows better still wins), and
    are left UNKNOWN ("") when there is no name to derive them from -- deriving
    from nothing would manufacture a carrier the observation never had.
    """
    name = str(obs.get("packaging") or "").strip()
    carrier = obs.get("carrier")
    reel = _clean_is_reel(obs.get("is_reel"))
    if name:
        if carrier is None:
            carrier = carrier_of(name)
        if reel is None:
            reel = is_reel(name)
    return {
        "packaging": name,
        "carrier": str(carrier).strip() if carrier else "",
        "is_reel": "" if reel is None else ("1" if reel else "0"),
        "reel_qty": _fmt_reel_qty(obs.get("reel_qty")),
        "reel_fee": _fmt_reel_fee(obs.get("reel_fee")),
    }


def migrate_observations(events_dir: str) -> bool:
    """Bring an existing observations CSV up to the current header.

    Idempotent: a file already carrying `FIELDNAMES` is left byte-for-byte
    alone (`csv_io.migrate_csv_header` compares the header sets and returns
    without writing). Returns False when there is no file to migrate.
    """
    csv_path = os.path.join(events_dir, OBSERVATIONS_FILE)
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return False
    csv_io.migrate_csv_header(csv_path, FIELDNAMES)
    return True


def record_observations(
    events_dir: str,
    observations: list[dict[str, Any]],
) -> None:
    """Append price observations to the event log CSV.

    Goes through `csv_io.append_csv_rows`, which migrates an older header
    before appending -- without that, a deployment holding the pre-packaging
    8-column file would get 13-value rows written under an 8-column header.
    """
    os.makedirs(events_dir, exist_ok=True)
    csv_path = os.path.join(events_dir, OBSERVATIONS_FILE)
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for obs in observations:
        rows.append({
            "timestamp": obs.get("timestamp", ts),
            "part_id": obs["part_id"],
            "distributor": obs.get("distributor", ""),
            "unit_price": obs.get("unit_price", ""),
            "currency": obs.get("currency", ""),
            "source": obs.get("source", ""),
            "moq": obs.get("moq", ""),
            "note": obs.get("note", ""),
            **_packaging_columns(obs),
        })
    csv_io.append_csv_rows(csv_path, FIELDNAMES, rows)


def read_observations(
    events_dir: str,
    part_id: str | None = None,
) -> list[dict[str, str]]:
    """Read price observations, optionally filtered by part_id.

    Rows are normalized to the full current schema, so a row written before
    the packaging columns existed reads back with those columns present and
    empty -- "packaging unknown" -- rather than with the keys missing. Reading
    never rewrites the file; migration happens on the append path.
    """
    csv_path = os.path.join(events_dir, OBSERVATIONS_FILE)
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        raw_rows = list(csv.DictReader(f))
    rows = [{fn: (row.get(fn) or "") for fn in FIELDNAMES} for row in raw_rows]
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
        # Resolve distributor PN to inventory part_id.
        # When known_pids is non-empty we can validate; when empty (no parts in
        # inventory) every part_id is unknown so skip all observations to avoid
        # FK constraint failures on the prices table.
        if not known_pids:
            continue
        if pid not in known_pids:
            resolved = dist_to_pid.get(pid)
            if resolved:
                pid = resolved
            else:
                # Expected, not anomalous: quotes are recorded for parts that
                # were never stocked (see record_fetched_prices), and the
                # prices cache only serves inventory rows. Warning here would
                # emit one line per BOM part on every refresh.
                logger.debug("populate_prices_cache: no inventory part for %r", pid)
                continue
        try:
            price = float(obs["unit_price"])
        except (ValueError, TypeError):
            continue
        key = (pid, dist)
        if key not in agg:
            agg[key] = {"prices": [], "last_observed": "", "source": "",
                        "moq": None, "moq_packagings": set()}
        agg[key]["prices"].append(price)
        agg[key]["last_observed"] = obs.get("timestamp", "")
        agg[key]["source"] = obs.get("source", "")
        moq = obs.get("moq", "")
        if moq:
            try:
                agg[key]["moq"] = int(moq)
            except (ValueError, TypeError):
                pass
            else:
                # Which packagings the surviving `moq` could have come from.
                # Only moq-BEARING rows count: import/manual observations carry
                # no moq and never influence the value, so letting them widen
                # this set would null out a column they don't contribute to.
                agg[key]["moq_packagings"].add(
                    (obs.get("packaging") or "").strip().casefold())

    for (pid, dist), data in agg.items():
        prices = data["prices"]
        latest = prices[-1]
        avg = sum(prices) / len(prices)
        # `moq` is one scalar per (part_id, distributor), filled last-row-wins.
        # That was already only loosely meaningful, but with per-packaging
        # ladders recorded it becomes actively misleading: the surviving value
        # is the top break of whichever packaging happened to be written last,
        # so a part you can buy one of as cut tape would report a 3,000-part
        # reel quantity. Two packagings' break quantities have no common
        # scalar answer -- 1 and 3000 have no useful midpoint and a caller
        # cannot tell which it got -- so the honest answer is NULL, which the
        # column is already nullable for and which every consumer already
        # handles (an import-only part has always stored NULL here).
        # `cart_qty.tier_ladders` is the packaging-aware replacement.
        moq = data["moq"] if len(data["moq_packagings"]) <= 1 else None
        conn.execute(
            """INSERT OR REPLACE INTO prices
               (part_id, distributor, latest_unit_price, avg_unit_price,
                price_count, last_observed, moq, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, dist, latest, avg, len(prices),
             data["last_observed"], moq, data["source"]),
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


def _tier_observations(
    part_id: str,
    distributor: str,
    tiers: list[dict[str, Any]],
    packaging: dict[str, Any] | None = None,
    reel_qty: Any = None,
    reel_fee: Any = None,
) -> list[dict[str, Any]]:
    """One observation per price break of one ladder, tagged with its packaging.

    `packaging` is a normalized-product `packagings` entry (see
    `domain.product.annotate_packagings`): `name` / `carrier` / `isReel`, plus
    LCSC's per-entry `packetQty`. None means "this distributor published a
    single unlabelled ladder", which stays UNKNOWN rather than being guessed.
    """
    pkg = packaging or {}
    observations: list[dict[str, Any]] = []
    for tier in tiers or []:
        try:
            price = float(tier.get("price", 0))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        observations.append({
            "part_id": part_id,
            "distributor": distributor,
            "unit_price": price,
            "source": "live_fetch",
            "moq": tier.get("qty", ""),
            "packaging": pkg.get("name", ""),
            "carrier": pkg.get("carrier"),
            "is_reel": pkg.get("isReel"),
            # Per-packaging packet quantity when the distributor gives one
            # (LCSC), else the product-level factory reel quantity.
            "reel_qty": pkg.get("packetQty") if pkg.get("packetQty") else reel_qty,
            "reel_fee": reel_fee,
        })
    return observations


def record_fetched_prices(
    conn: sqlite3.Connection,
    events_dir: str,
    part_key: str,
    distributor: str,
    price_tiers: list[dict[str, Any]],
    packagings: list[dict[str, Any]] | None = None,
    reel_qty: Any = None,
    reel_fee: Any = None,
) -> None:
    """Record prices fetched from a distributor API/scraper.

    `packagings` / `reel_qty` / `reel_fee` come straight off the normalized
    product (`packagings` / `reelQty` / `reelFee`) and are all optional, so
    every existing caller keeps working and records exactly what it recorded
    before, with the packaging columns empty.

    When `packagings` carries per-packaging ladders -- DigiKey publishes a
    separate ladder for Cut Tape, Tape & Reel and Tape & Box -- EVERY ladder is
    recorded, each tagged with its own packaging, instead of only the active
    one. `price_tiers` is then ignored: the normalizers set the product's
    `prices` to the active packaging's ladder, so recording both would double
    every row of it. A `packagings` list whose entries have no prices (the
    DigiKey DOM-only scrape sees names but no per-packaging tiers) falls back
    to `price_tiers` with the packaging left unknown -- the names are known but
    which ladder belongs to which is not.
    """
    # A part you do not stock can still be quoted, and that quote is exactly
    # the evidence a BOM-built cart plans from -- so an unknown key is recorded
    # under itself rather than dropped. Every reader in this file already spells
    # the fallback this way; only this writer treated "not in inventory" as a
    # reason to discard an observation, which silently made the hover tooltip a
    # no-op on precisely the rows a BOM turns up. `populate_prices_cache` below
    # skips these for the `prices` cache, whose part_id has an FK into `parts`;
    # `price_observations.csv` has no such constraint and is the file the plan
    # actually reads.
    resolved_key = resolve_part_key(conn, part_key) or part_key
    os.makedirs(events_dir, exist_ok=True)

    observations: list[dict[str, Any]] = []
    for pkg in packagings or []:
        if not isinstance(pkg, dict) or not pkg.get("prices"):
            continue
        observations.extend(_tier_observations(
            resolved_key, distributor, pkg.get("prices") or [],
            packaging=pkg, reel_qty=reel_qty, reel_fee=reel_fee,
        ))
    if not observations:
        observations = _tier_observations(
            resolved_key, distributor, price_tiers,
            reel_qty=reel_qty, reel_fee=reel_fee,
        )

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


# Distributor key → purchase_ledger.csv column header.
_LEDGER_PN_COLS = {
    "lcsc": "LCSC Part Number",
    "digikey": "Digikey Part Number",
    "mouser": "Mouser Part Number",
    "pololu": "Pololu Part Number",
}
_DISTRIBUTOR_ORDER = ("lcsc", "digikey", "mouser", "pololu")


def get_sourced_distributors(
    conn: sqlite3.Connection, purchase_csv: str, part_key: str
) -> list[dict[str, str]]:
    """Distributors a part was sourced from: union of (record PNs) ∪ (ledger PNs).

    Returns one entry per distributor, deduped, in fixed order. Each entry's
    part_number prefers the current record PN, falling back to the most recent
    matching purchase-ledger PN. Returns [] when nothing matches.
    """
    resolved = resolve_part_key(conn, part_key) or part_key

    # ── record PNs (has-PN set) ──
    record: dict[str, str] = {}
    known_pns: set[str] = {resolved}
    try:
        row = conn.execute(
            "SELECT lcsc, digikey, mouser, pololu, mpn FROM parts WHERE part_id = ?",
            (resolved,),
        ).fetchone()
    except sqlite3.Error:
        logger.warning("get_sourced_distributors: parts query failed for %r", part_key)
        row = None
    if row is not None:
        for dist in _DISTRIBUTOR_ORDER:
            val = (row[dist] or "").strip()
            if val:
                record[dist] = val
                known_pns.add(val)
        mpn = (row["mpn"] or "").strip()
        if mpn:
            known_pns.add(mpn)

    # ── ledger PNs (purchased set) — most recent row per distributor wins ──
    ledger: dict[str, str] = {}
    if os.path.exists(purchase_csv):
        with open(purchase_csv, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                row_pns = {(r.get(c) or "").strip() for c in _LEDGER_PN_COLS.values()}
                row_pns.add((r.get("Manufacture Part Number") or "").strip())
                row_pns.discard("")
                if not (row_pns & known_pns):
                    continue
                for dist, col in _LEDGER_PN_COLS.items():
                    val = (r.get(col) or "").strip()
                    if val:
                        ledger[dist] = val  # later row = more recent → overwrites

    # ── union, record PN preferred ──
    out: list[dict[str, str]] = []
    for dist in _DISTRIBUTOR_ORDER:
        pn = record.get(dist) or ledger.get(dist)
        if pn:
            out.append({"distributor": dist, "part_number": pn})
    return out


def get_sourced_distributors_batch(
    conn: sqlite3.Connection, purchase_csv: str, part_keys: list[str]
) -> dict[str, list[dict[str, str]]]:
    """Batched ``get_sourced_distributors`` — reads the purchase ledger ONCE.

    Equivalent to calling ``get_sourced_distributors`` per key, but the ledger
    CSV is scanned a single time for the whole batch (the per-call version
    re-reads it every time). Use this when resolving many parts at once (e.g.
    enriching all cart items on a ``list_carts``), where the per-call version's
    N full-CSV reads would be wasteful. Result is keyed by the ORIGINAL key
    passed in (not the resolved canonical id).
    """
    # ── read ledger once: (row_pns, {dist: pn}) per row, file order preserved ──
    ledger_rows: list[tuple[set[str], dict[str, str]]] = []
    if os.path.exists(purchase_csv):
        with open(purchase_csv, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                row_pns = {(r.get(c) or "").strip() for c in _LEDGER_PN_COLS.values()}
                row_pns.add((r.get("Manufacture Part Number") or "").strip())
                row_pns.discard("")
                dists = {
                    dist: (r.get(col) or "").strip()
                    for dist, col in _LEDGER_PN_COLS.items()
                    if (r.get(col) or "").strip()
                }
                if row_pns and dists:
                    ledger_rows.append((row_pns, dists))

    out: dict[str, list[dict[str, str]]] = {}
    for key in dict.fromkeys(part_keys):  # dedupe, preserve first-seen order
        resolved = resolve_part_key(conn, key) or key
        record: dict[str, str] = {}
        known_pns: set[str] = {resolved}
        try:
            row = conn.execute(
                "SELECT lcsc, digikey, mouser, pololu, mpn FROM parts WHERE part_id = ?",
                (resolved,),
            ).fetchone()
        except sqlite3.Error:
            logger.warning("get_sourced_distributors_batch: parts query failed for %r", key)
            row = None
        if row is not None:
            for dist in _DISTRIBUTOR_ORDER:
                val = (row[dist] or "").strip()
                if val:
                    record[dist] = val
                    known_pns.add(val)
            mpn = (row["mpn"] or "").strip()
            if mpn:
                known_pns.add(mpn)

        ledger: dict[str, str] = {}
        for row_pns, dists in ledger_rows:  # file order → later row = more recent
            if row_pns & known_pns:
                ledger.update(dists)

        entries: list[dict[str, str]] = []
        for dist in _DISTRIBUTOR_ORDER:
            pn = record.get(dist) or ledger.get(dist)
            if pn:
                entries.append({"distributor": dist, "part_number": pn})
        out[key] = entries
    return out
