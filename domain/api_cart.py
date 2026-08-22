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
from domain import cart_plan
from domain.pricing import get_sourced_distributors_batch, resolve_part_key
from domain.purchase_candidates import PRESET_MIN, offers_from_ladders


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

    def _enrich_available(self, carts_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach ``available_distributors`` (list[str]) to every cart item.

        Sourceability matches split/consolidate/export: the union of a part's
        record PNs and its purchase-ledger PNs (via get_sourced_distributors),
        so the per-line dropdown offers ledger-only distributors the client's
        inventory-record view alone would miss. Raw items (no part_id) get [].
        Uses the batched helper so the ledger CSV is read once per call, not
        once per item.
        """
        part_ids = [
            it["part_id"]
            for cart in carts_list
            for it in cart["items"]
            if it.get("part_id")
        ]
        batch = (
            get_sourced_distributors_batch(self._api._get_cache(), self._api.input_csv, part_ids)
            if part_ids else {}
        )
        # Quoted-only distributors count as available too, or the plan would
        # recommend a distributor the line's own dropdown says it cannot use.
        quoted = cart_qty.observed_distributors_batch(self._api.events_dir, part_ids)
        for cart in carts_list:
            for it in cart["items"]:
                pid = it.get("part_id")
                if not pid:
                    it["available_distributors"] = []
                    continue
                avail = [e["distributor"] for e in batch.get(pid, [])]
                avail.extend(d for d in quoted.get(pid, []) if d not in avail)
                it["available_distributors"] = avail
        return carts_list

    def _part_meta(self, part_id: str | None) -> dict[str, Any]:
        if not part_id:
            return {}
        conn = self._api._get_cache()
        # part_id may be a distributor-specific alias (e.g. an invPartKey that
        # differs from the registry's canonical part_id) — resolve it the same
        # alias-aware way get_sourced_distributors() does before the direct
        # `parts` lookup, so aliased parts don't export with blank metadata.
        resolved = resolve_part_key(conn, part_id) or part_id
        row = conn.execute(
            "SELECT mpn, manufacturer, package, description FROM parts WHERE part_id=?",
            (resolved,),
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
            return self._enrich_available(carts.list_carts(self._api._get_cache()))

    def get_cart(self, cart_id: str) -> dict[str, Any]:
        with self._api._lock:
            cart = carts.get(self._api._get_cache(), cart_id)
            if cart is None:
                raise KeyError(cart_id)
            self._enrich_available([cart])
            return cart

    def create_cart(self, name: str | None) -> dict[str, Any]:
        with self._api._lock:
            resolved = name or datetime.now().strftime("Cart %Y-%m-%d %H:%M:%S")
            return carts.create(self._api._get_cache(), self._api.base_dir, resolved)

    def rename_cart(self, cart_id: str, name: str) -> dict[str, Any]:
        with self._api._lock:
            return carts.rename(self._api._get_cache(), self._api.base_dir, cart_id, name)

    def set_cart_board_count(self, cart_id: str, board_count: int) -> dict[str, Any]:
        with self._api._lock:
            return carts.set_board_count(
                self._api._get_cache(), self._api.base_dir, cart_id, board_count)

    # ── purchase plan ────────────────────────────────────────────────────

    def _on_hand(self, part_id: str) -> int | None:
        """Units of `part_id` on the shelf, or None if the part is unknown.

        None and 0 are kept apart: an unknown part has had nothing counted,
        while a known one at 0 has been counted and is empty. Both reduce the
        requirement by nothing, but only the first is missing information.
        """
        conn = self._api._get_cache()
        resolved = resolve_part_key(conn, part_id) or part_id
        row = conn.execute(
            "SELECT quantity FROM stock WHERE part_id=?", (resolved,)
        ).fetchone()
        return None if row is None else int(row["quantity"] or 0)

    def _quotable_distributors(self, part_id: str | None, resolved: str) -> list[str]:
        """Every distributor that could price this part: sourced, then quoted.

        Sourced distributors come first so a part you actually buy keeps
        offering its usual supplier first; quoted-only ones are appended.
        Planning is the one place that must look past `get_sourced_distributors`
        -- see `cart_qty.observed_distributors` for why.
        """
        wanted = self._part_distributors(part_id)
        seen = set(wanted)
        for dist in cart_qty.observed_distributors(self._api.events_dir, resolved):
            if dist not in seen:
                seen.add(dist)
                wanted.append(dist)
        return wanted

    def _offers(self, part_id: str, distributor: str | None) -> list:
        """Every purchasable offer for a part, across one or all distributors.

        Stock is left unset: `price_observations.csv` records prices, not
        availability, and a ladder is not evidence that anything is on a shelf.
        Every candidate therefore reports `stock_known: false` rather than
        implying an availability nobody observed.
        """
        conn = self._api._get_cache()
        resolved = resolve_part_key(conn, part_id) or part_id
        wanted = ([distributor] if distributor
                  else self._quotable_distributors(part_id, resolved))
        offers = []
        for dist in wanted:
            if not dist:
                continue
            groups = cart_qty.tier_ladders(self._api.events_dir, resolved, dist)
            offers.extend(offers_from_ladders(groups, dist))
        return offers

    def plan_cart(self, cart_id: str, preset: str = PRESET_MIN,
                  reel_ceiling: float | None = None) -> dict[str, Any]:
        """What to buy for one cart, per line, with every option that lost.

        Read-only: it recommends, it does not write. The quantities a user
        accepts are committed by the ordinary item update, so re-planning after
        a price refresh can never silently rewrite a decision they already made.
        """
        with self._api._lock:
            cart = carts.get(self._api._get_cache(), cart_id)
            if cart is None:
                raise KeyError(cart_id)
            return cart_plan.plan_cart(
                cart,
                offers_for=self._offers,
                on_hand_for=self._on_hand,
                default_preset=preset,
                reel_ceiling=reel_ceiling,
            )

    def delete_cart(self, cart_id: str) -> None:
        with self._api._lock:
            carts.delete(self._api._get_cache(), self._api.base_dir, cart_id)

    # ── active cart ──────────────────────────────────────────────────────

    def set_active_cart(self, identity: str, cart_id: str) -> dict[str, Any]:
        with self._api._lock:
            carts.set_active(self._api._get_cache(), self._api.base_dir, identity, cart_id)
            return {"active_cart_id": cart_id}

    def get_active_cart(self, identity: str) -> str | None:
        with self._api._lock:
            return carts.get_active(self._api.base_dir, identity)

    # ── items ────────────────────────────────────────────────────────────

    def add_cart_item(self, cart_id: str, part_id: str | None = None,
                       raw: dict | None = None, qty: int | None = None,
                       target_distributor: str | None = None,
                       shortfall: int | None = None,
                       target_packaging: str | None = None,
                       preset: str | None = None,
                       per_board_qty: int | None = None) -> dict[str, Any]:
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
                target_packaging=target_packaging, preset=preset,
                per_board_qty=per_board_qty,
            )

    def update_cart_item(self, cart_id: str, ref: str, qty: int | None = None,
                          target_distributor: str | None = None,
                          target_packaging: str | None = None,
                          preset: str | None = None,
                          per_board_qty: int | None = None) -> dict[str, Any]:
        with self._api._lock:
            return carts.update_item(
                self._api._get_cache(), self._api.base_dir, cart_id, ref,
                qty=qty, target_distributor=target_distributor,
                target_packaging=target_packaging, preset=preset,
                per_board_qty=per_board_qty,
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
                per_board_qty=entry.get("per_board_qty"),
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
