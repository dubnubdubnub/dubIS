"""InventoryCRUD facade — the shared-lock / cache hot path for inventory mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import domain.inventory
import inventory_ops

if TYPE_CHECKING:
    from domain.schema import InventoryItem


class InventoryCRUDFacade:
    def __init__(self, api) -> None:
        self._api = api

    def rollback_source(self, source: str) -> list[dict]:
        """Remove all adjustments with the given source tag and rebuild."""
        with self._api._lock:
            removed = inventory_ops.rollback_source(self._api.adjustments_csv, source)
            if removed:
                self._api._rebuild()
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
        return result

    def adjust_part(self, adj_type: str, part_key: str, quantity: int | str,
                    note: str = "", source: str = "") -> "list[InventoryItem]":
        """Set/add/remove adjustment. Returns fresh inventory."""
        with self._api._lock:
            return domain.inventory.adjust_part(
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

    def consume_bom(self, matches_json: str | list[dict[str, Any]],
                    board_qty: int | str, bom_name: str,
                    note: str = "", source: str = "") -> "list[InventoryItem]":
        """Consume matched BOM parts. Returns fresh inventory."""
        matches = self._api._ensure_parsed(matches_json)
        with self._api._lock:
            return domain.inventory.consume_bom(
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

    def remove_last_purchases(self, count: int | str) -> "list[InventoryItem]":
        """Remove the last `count` rows from purchase_ledger.csv and rebuild inventory."""
        return self._api._truncate_csv(self._api.input_csv, int(count), "purchase ledger")

    def remove_last_adjustments(self, count: int | str) -> "list[InventoryItem]":
        """Remove the last `count` rows from adjustments.csv and rebuild inventory."""
        return self._api._truncate_csv(self._api.adjustments_csv, int(count), "adjustments")

    def import_purchases(self, rows_json: str | list[dict[str, str]]) -> "list[InventoryItem]":
        """Append purchase rows to purchase_ledger.csv. Returns fresh inventory."""
        rows = self._api._ensure_parsed(rows_json)
        with self._api._lock:
            return domain.inventory.import_purchases(
                rows=rows,
                fieldnames=self._api.FIELDNAMES,
                input_csv=self._api.input_csv,
                events_dir=self._api.events_dir,
                adjustments_csv=self._api.adjustments_csv,
                adj_fieldnames=self._api.ADJ_FIELDNAMES,
                base_dir=self._api.base_dir,
                conn=self._api._get_cache(),
                distributors=self._api._distributors,
            )

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
