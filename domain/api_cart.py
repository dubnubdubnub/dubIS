"""Cart facade — CRUD, active-cart tracking, default qty, split/consolidate/export.

Mirrors PurchaseOrdersFacade/GenericPartsFacade: thin delegate onto the
``carts``/``cart_qty``/``cart_export`` modules, using the api's shared SQLite
connection (``self._api._get_cache()``) and ``base_dir`` (the JSON-overlay
data dir, matching ``carts.json``/``cart_active.json``). Mutating methods do
NOT rebuild inventory — carts are not part of the inventory materialized view.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import cart_export
import cart_qty
import carts


class CartFacade:
    def __init__(self, api) -> None:
        self._api = api

    # ── helpers ──────────────────────────────────────────────────────────

    def _part_distributors(self, part_id: str | None) -> list[str]:
        if not part_id:
            return []
        sourced = self._api.get_sourced_distributors(part_id)
        return [s["distributor"] for s in sourced]

    def _resolve_pn(self, part_id: str | None, distributor: str) -> str | None:
        if not part_id:
            return None
        for s in self._api.get_sourced_distributors(part_id):
            if s["distributor"] == distributor:
                return s["part_number"]
        return None

    def _part_meta(self, part_id: str | None) -> dict[str, Any]:
        if not part_id:
            return {}
        row = self._api._get_cache().execute(
            "SELECT mpn, manufacturer, package, description FROM parts WHERE part_id=?",
            (part_id,),
        ).fetchone()
        if row is None:
            return {}
        return {
            "mpn": row["mpn"],
            "manufacturer": row["manufacturer"],
            "package": row["package"],
            "description": row["description"],
        }

    # ── CRUD ─────────────────────────────────────────────────────────────

    def list_carts(self) -> list[dict[str, Any]]:
        with self._api._lock:
            return carts.list_carts(self._api._get_cache())

    def get_cart(self, cart_id: str) -> dict[str, Any]:
        with self._api._lock:
            cart = carts.get(self._api._get_cache(), cart_id)
            if cart is None:
                raise KeyError(cart_id)
            return cart

    def create_cart(self, name: str | None) -> dict[str, Any]:
        with self._api._lock:
            resolved = name or datetime.now().strftime("Cart %Y-%m-%d %H:%M:%S")
            return carts.create(self._api._get_cache(), self._api.base_dir, resolved)

    def rename_cart(self, cart_id: str, name: str) -> dict[str, Any]:
        with self._api._lock:
            return carts.rename(self._api._get_cache(), self._api.base_dir, cart_id, name)

    def delete_cart(self, cart_id: str) -> None:
        with self._api._lock:
            carts.delete(self._api._get_cache(), self._api.base_dir, cart_id)

    # ── active cart ──────────────────────────────────────────────────────

    def set_active_cart(self, identity: str, cart_id: str) -> dict[str, Any]:
        with self._api._lock:
            carts.set_active(self._api.base_dir, identity, cart_id)
            return {"active_cart_id": cart_id}

    def get_active_cart(self, identity: str) -> str | None:
        with self._api._lock:
            return carts.get_active(self._api.base_dir, identity)

    # ── items ────────────────────────────────────────────────────────────

    def add_cart_item(self, cart_id: str, part_id: str | None = None,
                       raw: dict | None = None, qty: int | None = None,
                       target_distributor: str | None = None,
                       shortfall: int | None = None) -> dict[str, Any]:
        with self._api._lock:
            conn = self._api._get_cache()
            if qty is None:
                dist = target_distributor
                if dist is None and part_id:
                    sourced = self._api.get_sourced_distributors(part_id)
                    dist = sourced[0]["distributor"] if sourced else None
                ladder = (
                    cart_qty.tier_ladder(self._api.events_dir, part_id, dist)
                    if (part_id and dist) else []
                )
                qty = cart_qty.default_qty(shortfall, ladder)
            return carts.add_item(
                conn, self._api.base_dir, cart_id, part_id=part_id, raw=raw,
                qty=qty, target_distributor=target_distributor,
            )

    def update_cart_item(self, cart_id: str, ref: str, qty: int | None = None,
                          target_distributor: str | None = None) -> dict[str, Any]:
        with self._api._lock:
            return carts.update_item(
                self._api._get_cache(), self._api.base_dir, cart_id, ref,
                qty=qty, target_distributor=target_distributor,
            )

    def remove_cart_item(self, cart_id: str, ref: str) -> dict[str, Any]:
        with self._api._lock:
            return carts.remove_item(self._api._get_cache(), self._api.base_dir, cart_id, ref)

    def clear_cart(self, cart_id: str) -> dict[str, Any]:
        with self._api._lock:
            return carts.clear(self._api._get_cache(), self._api.base_dir, cart_id)

    def add_bom_missing_to_cart(self, cart_id: str, missing: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for entry in missing:
            result = self.add_cart_item(
                cart_id,
                part_id=entry.get("part_id"),
                raw=entry.get("raw"),
                qty=entry.get("qty"),
                target_distributor=entry.get("target_distributor"),
                shortfall=entry.get("shortfall"),
            )
        if not result:
            result = self.get_cart(cart_id)
        return result

    # ── split / consolidate / export ─────────────────────────────────────

    def split_cart(self, cart_id: str, distributor: str, new_name: str,
                    remove_from_source: bool) -> dict[str, Any]:
        with self._api._lock:
            return carts.split_by_distributor(
                self._api._get_cache(), self._api.base_dir, cart_id, distributor,
                new_name, remove_from_source, self._part_distributors,
            )

    def consolidate_cart(self, cart_id: str, distributor: str) -> dict[str, Any]:
        with self._api._lock:
            return carts.consolidate(
                self._api._get_cache(), self._api.base_dir, cart_id, distributor,
                self._part_distributors,
            )

    def export_cart(self, cart_id: str, distributor: str, fmt: str) -> dict[str, Any]:
        with self._api._lock:
            cart = carts.get(self._api._get_cache(), cart_id)
            if cart is None:
                raise KeyError(cart_id)
            return cart_export.build(
                cart["items"], distributor, fmt, self._resolve_pn, self._part_meta,
            )
