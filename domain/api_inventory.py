"""InventoryCRUD facade — the shared-lock / cache hot path for inventory mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import domain.inventory
import inventory_ops
from dubis_errors import NotFoundError

if TYPE_CHECKING:
    from domain.schema import InventoryItem


def _unmatched_tag_error(tag: str, csv_path: str, label: str) -> NotFoundError:
    """Build the "nothing carried that tag" error, naming what the server holds.

    The production failure this exists for: the operator ran the documented
    `dubis adjustments rollback-source xfer-to-aspen-20261014`, the rows were
    stored as `xfer-to-aspen-20261014@root`, and the route answered 200 with an
    empty list. A zero-count undo is not a success, and the message has to
    close the gap between the tag typed and the tag stored — so it lists the
    tags that ARE there.
    """
    known = inventory_ops.distinct_sources(csv_path)
    shown = ", ".join(repr(tag_value) for tag_value in known[:10]) or "(none at all)"
    more = f" (+{len(known) - 10} more)" if len(known) > 10 else ""
    return NotFoundError(
        f"No {label} are tagged {tag!r} on this server, so nothing was undone. "
        f"Tags this server holds: {shown}{more}. A tag written by a non-local "
        f"caller is stored as `<tag>@<identity>`; naming the bare tag matches "
        f"every identity that used it, so a typo here is the usual cause."
    )


class InventoryCRUDFacade:
    def __init__(self, api) -> None:
        self._api = api

    def rollback_source(self, source: str, *,
                        require_match: bool = False) -> list[dict]:
        """Remove all adjustments with the given source tag and rebuild.

        `require_match` is what the /v1 undo route passes: a rollback that
        removed nothing raises instead of answering 200 with an empty list.
        `python -m server --rollback-on-exit` deliberately leaves it False — a
        test session that made no adjustments has nothing to clean up, and a
        traceback on every clean shutdown would bury the shutdown line.
        """
        with self._api._lock:
            removed = inventory_ops.rollback_source(self._api.adjustments_csv, source)
            if removed:
                self._api._rebuild()
            elif require_match:
                raise _unmatched_tag_error(
                    source, self._api.adjustments_csv, "adjustments")
        return removed

    def rollback_purchases(self, source: str, *,
                           require_match: bool = False) -> list[dict]:
        """Remove all purchase-ledger rows with the given source tag and rebuild.

        Only the ledger rows travel back. Price observations the import recorded
        stay in `events/price_observations.csv`: a price is a *sighting* and that
        log is append-only by design (docs/entity-store.md), so undoing a
        purchase must not rewrite history that was true when it was seen.
        """
        with self._api._lock:
            removed = inventory_ops.rollback_purchases(self._api.input_csv, source)
            if removed:
                self._api._rebuild()
            elif require_match:
                raise _unmatched_tag_error(
                    source, self._api.input_csv, "purchase-ledger rows")
        return removed

    def rebuild_inventory(self) -> "list[InventoryItem]":
        """Rebuild inventory. Uses catch-up if cache exists, full rebuild otherwise."""
        result, migration_summary = domain.inventory.rebuild_or_catchup(
            base_dir=self._api.base_dir,
            input_csv=self._api.input_csv,
            adjustments_csv=self._api.adjustments_csv,
            events_dir=self._api.events_dir,
            fieldnames=self._api.FIELDNAMES,
            adj_fieldnames=self._api.ADJ_FIELDNAMES,
            conn=self._api._get_cache(),
        )
        if migration_summary:
            self._api._last_migration_summary = migration_summary
        self._api._push_to_mirror(result)
        return result

    def adjust_part(self, adj_type: str, part_key: str, quantity: int | str,
                    note: str = "", source: str = "") -> "list[InventoryItem]":
        """Set/add/remove adjustment. Returns fresh inventory."""
        with self._api._lock:
            result = domain.inventory.adjust_part(
                adj_type=adj_type,
                part_key=part_key,
                quantity=int(quantity),
                note=note,
                source=source,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                input_csv=self._api.input_csv,
                events_dir=self._api.events_dir,
                fieldnames=self._api.FIELDNAMES,
                conn=self._api._get_cache(),
            )
        self._api._push_to_mirror(result)
        return result

    def consume_bom(self, matches_json: str | list[dict[str, Any]],
                    board_qty: int | str, bom_name: str,
                    note: str = "", source: str = "") -> "list[InventoryItem]":
        """Consume matched BOM parts. Returns fresh inventory."""
        matches = self._api._ensure_parsed(matches_json)
        with self._api._lock:
            result = domain.inventory.consume_bom(
                matches=matches,
                board_qty=int(board_qty),
                bom_name=bom_name,
                note=note,
                source=source,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                input_csv=self._api.input_csv,
                events_dir=self._api.events_dir,
                fieldnames=self._api.FIELDNAMES,
                conn=self._api._get_cache(),
            )
        self._api._push_to_mirror(result)
        return result

    def remove_last_purchases(self, count: int | str) -> "list[InventoryItem]":
        """Remove the last `count` rows from purchase_ledger.csv and rebuild inventory."""
        return self._api._truncate_csv(self._api.input_csv, int(count), "purchase ledger")

    def remove_last_adjustments(self, count: int | str) -> "list[InventoryItem]":
        """Remove the last `count` rows from adjustments.csv and rebuild inventory."""
        return self._api._truncate_csv(self._api.adjustments_csv, int(count), "adjustments")

    def import_purchases(self, rows_json: str | list[dict[str, str]],
                         source: str = "") -> "list[InventoryItem]":
        """Append purchase rows to purchase_ledger.csv. Returns fresh inventory.

        `source` is the provenance tag that makes the rows undoable with
        `rollback_purchases` instead of only as "delete the last N rows".
        """
        rows = self._api._ensure_parsed(rows_json)
        with self._api._lock:
            result = domain.inventory.import_purchases(
                rows=rows,
                source=source,
                fieldnames=self._api.FIELDNAMES,
                input_csv=self._api.input_csv,
                events_dir=self._api.events_dir,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                conn=self._api._get_cache(),
                distributors=self._api._distributors,
            )
        self._api._push_to_mirror(result)
        return result

    def update_part_price(self, part_key: str, unit_price: float | None = None,
                          ext_price: float | None = None) -> "list[InventoryItem]":
        """Update unit price and ext price for a part in purchase_ledger.csv.
        Auto-calculates the missing price field if only one is provided.
        Returns fresh inventory after rebuild.
        """
        if unit_price is not None:
            unit_price = float(unit_price)
        if ext_price is not None:
            ext_price = float(ext_price)
        with self._api._lock:
            return domain.inventory.update_part_price(
                part_key=part_key,
                unit_price=unit_price,
                ext_price=ext_price,
                input_csv=self._api.input_csv,
                events_dir=self._api.events_dir,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                fieldnames=self._api.FIELDNAMES,
                conn=self._api._get_cache(),
                infer_distributor_for_key=self._api._infer_distributor_for_key,
            )

    def update_part_fields(self, part_key: str,
                           fields_json: str | dict[str, str]) -> "list[InventoryItem]":
        """Update metadata fields for a part in purchase_ledger.csv."""
        fields = self._api._ensure_parsed(fields_json)
        with self._api._lock:
            return domain.inventory.update_part_fields(
                part_key=part_key,
                fields=fields,
                field_to_col=self._api._FIELD_TO_COL,
                input_csv=self._api.input_csv,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                fieldnames=self._api.FIELDNAMES,
                events_dir=self._api.events_dir,
                conn=self._api._get_cache(),
            )

    def create_part(self, part_key: str, fields_json: str | dict[str, str],
                    unit_price: float | None = None) -> dict:
        """Create a part with identity, or complete one that has none.

        Returns the route's detail (`part_key`, `created`, `qty`, ...), NOT the
        inventory: the caller is a route that publishes an SSE event, and the
        event detail is not a place to ship a whole grid. The fresh inventory
        still goes to the mirror like every other mutation.
        """
        fields = self._api._ensure_parsed(fields_json)
        with self._api._lock:
            detail, inventory = domain.inventory.create_part(
                part_key=part_key,
                fields=fields,
                unit_price=unit_price,
                field_to_col=self._api._FIELD_TO_COL,
                input_csv=self._api.input_csv,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                fieldnames=self._api.FIELDNAMES,
                events_dir=self._api.events_dir,
                conn=self._api._get_cache(),
            )
        self._api._push_to_mirror(inventory)
        return detail

    def fetch_missing_descriptions(self) -> dict:
        """Fetch descriptions for parts with a distributor PN but none set."""
        dm = self._api._distributors
        # Wrapped so this bulk path persists parametric attributes too — same
        # hook the single-product fetch path gets in DistributorFacade.
        fetchers = {
            name: self._api._attrs.recording_fetcher(name, fetch)
            for name, fetch in (
                ("lcsc", dm.fetch_lcsc_product),
                ("digikey", dm.fetch_digikey_product),
                ("mouser", dm.fetch_mouser_product),
                ("pololu", dm.fetch_pololu_product),
            )
        }
        # Lock is held across the distributor network fetches, not just the CSV
        # read/write, so the read -> write -> rebuild stays atomic. This is
        # intentional: the operation is a small, user-initiated cleanup batch
        # (a handful of missing-description parts), not a bulk/background job -
        # don't "optimize" the fetches out from under the lock, it would break
        # atomicity for a workload that doesn't need the throughput.
        with self._api._lock:
            return domain.inventory.fetch_missing_descriptions(
                fetchers=fetchers,
                input_csv=self._api.input_csv,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                fieldnames=self._api.FIELDNAMES,
                events_dir=self._api.events_dir,
                conn=self._api._get_cache(),
            )

    def delete_part(self, part_key: str) -> "list[InventoryItem]":
        """Permanently delete a part with no purchase history. Returns fresh inventory."""
        with self._api._lock:
            result = domain.inventory.delete_part(
                part_key=part_key,
                input_csv=self._api.input_csv,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                fieldnames=self._api.FIELDNAMES,
                events_dir=self._api.events_dir,
                conn=self._api._get_cache(),
            )
        self._api._push_to_mirror(result)
        return result

    def has_purchase_history(self, part_key: str) -> bool:
        return domain.inventory.has_purchase_history(self._api.input_csv, part_key)
