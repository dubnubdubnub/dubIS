"""Distributor category store — durable "what does each distributor call this part".

Every distributor client already returns the part's own category and
subcategory on ``NormalizedProduct``; before this module the only reader was
the product-preview tooltip, so the answer was fetched, displayed once, and
dropped.  That is the one fact about a part a distributor states *about the
part rather than about its description string*, and it is the fact
``category_map.csv`` turns into a dubIS section -- so it has to survive the
fetch for ``categorize()`` to use it.

It has to survive it for a second, harder reason: ``categorize()`` runs inside
``domain.inventory.rebuild()``, which runs on every inventory mutation.  A
rebuild that fetched categories from the network would make adding five
capacitors to the ledger five HTTP round trips, and would fail every one of
them the moment the distributor blocked the scraper.  So the category is
recorded when a fetch happens to occur, and a rebuild only ever reads the
local file.

Storage follows the entity-store convention (docs/entity-store.md), in the
shape ``domain.attributes.py`` established:

* **Durable file** ``data/distributor_categories.csv`` — one row per
  (part_id, distributor), rewritten atomically through
  ``csv_io.atomic_write_rows``.  Re-fetching refreshes the row in place rather
  than appending: a category is a property of the part, and the useful record
  is the current one plus ``observed_at``, not every sighting.
* **SQLite table** ``distributor_categories`` in ``cache_db.create_schema`` —
  a derived, deletable mirror repopulated by :func:`load_into_db`.

One row per (part_id, distributor) rather than one per sighting is also what
makes the store editable: blanking a row is how you tell dubIS to stop
believing a distributor about one part.  That is the *local* opt-out, and it is
temporary -- the next fetch of that part rewrites the row from the live page.
A distributor that misfiles a part does so for everyone and on every fetch, so
the durable carve-out is the shipped list: ``category_refusals.csv``, read by
:func:`resolve` below and consulted before the map is asked anything.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Callable

import category_map
import category_refusals
import csv_io
from domain.pricing import resolve_part_key

logger = logging.getLogger(__name__)

CSV_FILE = "distributor_categories.csv"

FIELDNAMES = [
    "part_id",      # inventory part_id (or the raw fetch key if unresolvable)
    "distributor",  # whose taxonomy this is: lcsc / digikey / mouser / pololu
    "category",     # exactly as that distributor words it, never rewritten
    "subcategory",  # "" when the distributor exposed only one level
    "observed_at",  # local ISO timestamp of the fetch that wrote this row
    "source",       # how it was obtained (matches price observations' `source`)
]

DEFAULT_SOURCE = "live_fetch"

# Which distributor wins when two disagree about where a part belongs.
#
# Ordered by how much of their own taxonomy category_map.csv can actually
# place, which is measured rather than assumed: scripts/score-categorize-corpus.py
# reports the share of corpus parts whose category yields a single-section
# opinion (LCSC 68%, DigiKey 63%).  Mouser and Pololu sit last because their
# clients capture the leaf of a deep breadcrumb while the map is keyed on
# catalogue buckets, so their rows match least often -- and because LCSC is
# where this inventory is actually bought, which is the tie-break that matters
# in practice.
PREFERENCE = ("lcsc", "digikey", "mouser", "pololu")

_KEY_FIELDS = ("part_id", "distributor")


def csv_path(data_dir: str) -> str:
    return os.path.join(data_dir, CSV_FILE)


def _row_key(row: dict[str, str]) -> tuple[str, str]:
    return tuple((row.get(f) or "").strip() for f in _KEY_FIELDS)  # type: ignore[return-value]


def read_rows(data_dir: str, part_id: str | None = None) -> list[dict[str, str]]:
    """Read the durable CSV, optionally filtered by part_id. Missing file -> []."""
    path = csv_path(data_dir)
    if not os.path.exists(path):
        return []
    csv_io.migrate_csv_header(path, FIELDNAMES)
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{k: (row.get(k) or "") for k in FIELDNAMES} for row in csv.DictReader(f)]
    if part_id is not None:
        rows = [r for r in rows if r["part_id"] == part_id]
    return rows


def _write_rows(data_dir: str, rows: list[dict[str, str]]) -> None:
    os.makedirs(data_dir, exist_ok=True)
    normalized = [{k: row.get(k, "") for k in FIELDNAMES} for row in rows]
    csv_io.atomic_write_rows(csv_path(data_dir), FIELDNAMES, normalized, encoding="utf-8")


def record_categories(
    data_dir: str,
    part_id: str,
    distributor: str,
    category: str,
    subcategory: str = "",
    *,
    source: str = DEFAULT_SOURCE,
    observed_at: str | None = None,
) -> bool:
    """Upsert one part's category from one distributor.

    True when the file now says something it did not before -- a new row, or a
    row whose category or subcategory actually changed.  Re-fetching a part
    whose category is unchanged refreshes ``observed_at`` and returns False,
    which is what tells the caller a rebuild would produce the same inventory
    and is not worth doing.  (A preview is a read; making it cost a full
    rebuild every time someone hovers a part is not "persist what the fetch
    told us", it is a read path that writes the world.)

    An empty ``category`` *and* empty ``subcategory`` is a no-op rather than a
    blank row: a distributor that told us nothing should leave whatever another
    fetch recorded alone, not overwrite it with emptiness.
    """
    category = (category or "").strip()
    subcategory = (subcategory or "").strip()
    if not category and not subcategory:
        return False
    distributor = (distributor or "").strip().lower()
    if distributor not in category_map.DISTRIBUTORS:
        raise ValueError(
            f"cannot store a category from unknown distributor {distributor!r}; "
            f"category_map.csv only speaks {', '.join(category_map.DISTRIBUTORS)}"
        )
    row = {
        "part_id": part_id,
        "distributor": distributor,
        "category": category,
        "subcategory": subcategory,
        "observed_at": observed_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
    }
    key = _row_key(row)
    merged: list[dict[str, str]] = []
    replaced = False
    changed = False
    for existing in read_rows(data_dir):
        if _row_key(existing) == key:
            merged.append(row)
            replaced = True
            changed = (existing["category"] != category
                       or existing["subcategory"] != subcategory)
        else:
            merged.append(existing)
    if not replaced:
        merged.append(row)
        changed = True
    _write_rows(data_dir, merged)
    return changed


def index(rows: list[dict[str, str]]) -> dict[str, dict[str, category_map.DistributorCategory]]:
    """part_id -> {distributor: DistributorCategory}, newest sighting per pair.

    The CSV is one row per pair, but a hand-edited file can hold duplicates;
    keeping the row with the later ``observed_at`` means the answer does not
    depend on which of two duplicate lines sqlite happened to read last.
    """
    out: dict[str, dict[str, category_map.DistributorCategory]] = {}
    stamps: dict[tuple[str, str], str] = {}
    for row in rows:
        part_id = (row.get("part_id") or "").strip()
        distributor = (row.get("distributor") or "").strip().lower()
        if not part_id or distributor not in category_map.DISTRIBUTORS:
            continue
        pair = (part_id, distributor)
        stamp = row.get("observed_at") or ""
        if pair in stamps and stamp < stamps[pair]:
            continue
        stamps[pair] = stamp
        out.setdefault(part_id, {})[distributor] = category_map.DistributorCategory(
            distributor=distributor,
            category=(row.get("category") or "").strip(),
            subcategory=(row.get("subcategory") or "").strip(),
        )
    return out


def resolve(
    by_distributor: dict[str, category_map.DistributorCategory],
    *,
    part_id: str = "",
    refusals: category_refusals.Refusals | None = None,
) -> category_map.DistributorCategory | None:
    """The one category to believe, by :data:`PREFERENCE`.

    Only distributors the map has an opinion about are considered.  A DigiKey
    bucket the table calls ambiguous does not *beat* an LCSC bucket it can
    place -- it loses to it, because "no opinion" is not an opinion and
    letting it win would mean the more specific distributor's answer is
    discarded by the less specific one's silence.

    ``refusals`` is the shipped misfile list (``category_refusals.csv``).  A
    distributor whose answer for this part is on it is skipped here, which is
    the only place that decision can live: skipping is per *distributor*, so
    LCSC misfiling a part hands the decision to DigiKey, and if nobody else
    answered, the part is placed by its description -- the signal that was
    right about it.  ``None`` means "no list", which is what a caller with no
    part identity to look up has; production always passes one, through
    :func:`resolver`.
    """
    for distributor in PREFERENCE:
        found = by_distributor.get(distributor)
        if found is None:
            continue
        if refusals is not None and part_id:
            refused = refusals.refusal_for(part_id, found)
            if refused is not None:
                logger.info(
                    "not believing %s about %s (%s): %s", distributor, part_id,
                    " / ".join(x for x in (found.category, found.subcategory) if x),
                    refused.reason)
                continue
        if category_map.opinion(found.distributor, found.category, found.subcategory):
            return found
    return None


def resolver(
    data_dir: str,
    part_key: Callable[[dict[str, str]], str],
    *,
    refusals: category_refusals.Refusals | None = None,
) -> Callable[[dict[str, str]], category_map.DistributorCategory | None]:
    """A per-row lookup for one rebuild, reading the CSV exactly once.

    ``part_key`` is ``inventory_ops.get_part_key`` -- the store is keyed on the
    same identity the ledger uses, so a category recorded against an LCSC code
    is found when the row is keyed on its MPN instead.  Rows for parts no
    longer in inventory stay in the file (they cost nothing and a re-purchase
    wants them back) and simply never match.

    ``refusals`` defaults to the shipped misfile list, read once per rebuild
    beside the store.  An explicit ``Refusals([])`` says "there are no
    misfiles", which is a different statement from the default and the one a
    test isolating one mechanism means.
    """
    table = index(read_rows(data_dir))
    if not table:
        return lambda row: None
    if refusals is None:
        refusals = category_refusals.load()

    def lookup(row: dict[str, str]) -> category_map.DistributorCategory | None:
        key = part_key(row)
        if not key:
            return None
        found = table.get(key)
        return resolve(found, part_id=key, refusals=refusals) if found else None

    return lookup


def content_hash(data_dir: str) -> str:
    """A digest of what the cache derived from this store, ignoring timestamps.

    ``cache_db`` hashes its inputs so catch-up can tell whether the cache still
    describes them. Hashing the file's bytes would be wrong here: re-fetching a
    part refreshes ``observed_at`` without changing the category, and every
    product preview would then look like an input change and force a full
    rebuild of the whole inventory. So this covers exactly the columns
    ``categorize()`` reads, in a stable order.

    The shipped misfile list is folded in because it feeds the same column: a
    deploy that adds a refusal row has to force the rebuild that puts the
    protected part back, or the carve-out sits in the repo and changes nothing
    until some unrelated event happens to rebuild the cache.  With no store
    there is no opinion anywhere, so nothing to re-derive, and the digest stays
    empty for exactly the reason it was empty before.
    """
    if not os.path.exists(csv_path(data_dir)):
        return ""
    facts = sorted(
        (r["part_id"], r["distributor"], r["category"], r["subcategory"])
        for r in read_rows(data_dir)
    )
    digest = hashlib.sha256()
    for fact in facts:
        digest.update("\x1f".join(fact).encode("utf-8"))
        digest.update(b"\x1e")
    digest.update(b"|refusals|")
    digest.update(category_refusals.content_hash().encode("ascii"))
    return digest.hexdigest()


def load_into_db(conn: sqlite3.Connection, data_dir: str) -> None:
    """Rebuild the derived ``distributor_categories`` table from the durable CSV.

    Rows whose part_id is not in ``parts`` are skipped -- the table has a
    foreign key onto it, and a part we fetched but never purchased has no
    inventory row.  The CSV keeps them regardless: the durable store is the
    source of truth, the cache is what the FK constrains.
    """
    conn.execute("DELETE FROM distributor_categories")
    rows = read_rows(data_dir)
    if not rows:
        conn.commit()
        return
    known = {r[0] for r in conn.execute("SELECT part_id FROM parts")}
    skipped = 0
    for row in rows:
        if row["part_id"] not in known:
            skipped += 1
            continue
        conn.execute(
            """INSERT OR REPLACE INTO distributor_categories
               (part_id, distributor, category, subcategory, observed_at, source)
               VALUES (?,?,?,?,?,?)""",
            (row["part_id"], row["distributor"], row["category"],
             row["subcategory"], row["observed_at"], row["source"]),
        )
    if skipped:
        logger.warning(
            "load_into_db: skipped %d distributor-category row(s) for parts not in "
            "inventory", skipped)
    conn.commit()


def record_fetched_categories(
    conn: sqlite3.Connection,
    data_dir: str,
    part_key: str,
    distributor: str,
    product: dict[str, Any],
    *,
    source: str = DEFAULT_SOURCE,
) -> bool:
    """Persist the category one fetch returned. True if the answer changed.

    ``part_key`` is whatever the fetch was keyed on; it is resolved to the
    inventory part_id when possible so every distributor's row for one part
    shares a key.  An unresolvable key is *kept* under the raw key rather than
    dropped -- an alternate-part candidate we have never purchased is exactly
    what these rows are for (same reasoning as ``domain.attributes``).

    When the answer changed, the next rebuild has to be a full one.  Nothing
    else would notice: a rebuild takes the catch-up path whenever the ledger
    and adjustments are unchanged, and catch-up replays quantity events without
    re-categorizing anything.  So ``write_checkpoint`` now hashes this file too
    -- see ``cache_db.catch_up`` -- and a recorded category is what forces the
    rebuild that puts the part where it belongs, rather than sitting in the CSV
    until the user happens to edit the ledger weeks later.
    """
    category = str(product.get("category") or "").strip()
    subcategory = str(product.get("subcategory") or "").strip()
    if not category and not subcategory:
        return False
    resolved_key = resolve_part_key(conn, part_key) or part_key
    changed = record_categories(
        data_dir, resolved_key, distributor, category, subcategory, source=source)
    if changed:
        load_into_db(conn, data_dir)
    return changed


def get_part_categories(
    conn: sqlite3.Connection,
    data_dir: str,
    part_key: str,
    *,
    refusals: category_refusals.Refusals | None = None,
) -> list[dict[str, Any]]:
    """What each distributor calls this part, preferred first, with the verdict.

    ``section`` is what ``category_map.csv`` makes of it -- ``None`` when the
    bucket is ambiguous or nobody has labelled it, which is the honest answer
    and the one the UI has to render as "no opinion" rather than as a blank.
    ``note`` carries the reason a human wrote next to the row, so a reviewer
    asking "why is this an Other?" gets the sentence that was decided, not a
    guess at it.

    ``refused`` marks a row the shipped misfile list has struck out, with the
    row's ``refusal_reason`` saying why.  Without it this endpoint would show a
    section for a category that is not being used, which is the worst thing a
    "why is this part here?" endpoint can do: answer confidently about a signal
    nobody consulted.
    """
    if refusals is None:
        refusals = category_refusals.load()
    resolved_key = resolve_part_key(conn, part_key) or part_key
    found = index(read_rows(data_dir, resolved_key)).get(resolved_key, {})
    # `preferred` is resolve()'s answer, not "first row": the row that wins is
    # the first in PREFERENCE order the map can place and the refusal has not
    # struck out, and a reader comparing this list against the section the part
    # actually landed in needs the same rule, not a different one that happens
    # to agree most of the time.
    winner = resolve(found, part_id=resolved_key, refusals=refusals)
    preferred_key = (winner.distributor, winner.category, winner.subcategory) if winner else None
    out: list[dict[str, Any]] = []
    ordered = [d for d in PREFERENCE if d in found] + [
        d for d in sorted(found) if d not in PREFERENCE]
    for distributor in ordered:
        entry = found[distributor]
        refusal = refusals.refusal_for(resolved_key, entry)
        out.append({
            "distributor": entry.distributor,
            "category": entry.category,
            "subcategory": entry.subcategory,
            "section": category_map.opinion(
                entry.distributor, entry.category, entry.subcategory),
            "note": " ".join(category_map.load().notes(
                entry.distributor, entry.category, entry.subcategory)),
            "refused": refusal is not None,
            "refusal_reason": refusal.reason if refusal else "",
            "preferred": (entry.distributor, entry.category, entry.subcategory)
            == preferred_key,
        })
    return out
