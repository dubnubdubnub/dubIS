// @ts-check
/* cart-add.js — Cart-add mode: toggle beside the header cart icon; while
   active, clicking an inventory row adds that part to the active cart
   instead of the row's normal selection/linking behavior.

   Module-level `active` state (not a signal) — this is a transient UI mode
   local to this client, not cross-panel data, so EventBus/signals are
   unnecessary; cart-header.js and inv-row-build.js both import this module
   directly and call its functions synchronously. */

import { addToActiveCart } from './cart-store.js';
import { AppLog } from '../api.js';
import { invPartKey } from '../part-keys.js';
import { handleTrigger } from '../panel-collapse.js';

let active = false;

/** @returns {boolean} whether cart-add mode is currently active. */
export function isActive() {
  return active;
}

/** Flip cart-add mode and reflect it on <body> + the toggle button. */
export function toggle() {
  active = !active;
  // Turning the mode on surfaces the left panel, which carries the cart context.
  if (active) handleTrigger('CART_ADD_MODE');
  document.body.classList.toggle('cart-add-active', active);
  const btn = document.getElementById('cart-add-toggle');
  if (btn) {
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  }
}

/**
 * Called from the inventory row click handler. When cart-add mode is active,
 * adds the clicked part to the active cart and returns true (consumed) so the
 * caller skips its normal click behavior (selection/linking/etc). Returns
 * false when cart-add mode is off, so the caller falls through as usual.
 *
 * Uses `invPartKey(item)` (the same canonical LCSC/MPN/DigiKey/Pololu key
 * `row.dataset.partId` is built from in inv-row-build.js) rather than a
 * literal `item.part_id` field — InventoryItem carries no such field (see
 * js/inventory-record.d.ts); `part_id` is the cart API's/domain's name for
 * that same canonical key (see domain/api_cart.py's `add_cart_item`).
 * @param {import('../types.js').InventoryItem} item
 * @returns {boolean}
 */
export function handleRowClick(item) {
  if (!active) return false;
  addToActiveCart({ partId: invPartKey(item) }).catch((e) =>
    AppLog.error('cart-add: addToActiveCart failed: ' + e.message));
  return true;
}
