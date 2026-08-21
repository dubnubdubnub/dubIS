// @ts-check
/* cart-store.js — Cart data layer: fetches/mutates carts via the /v1 API,
   holds the last-loaded { carts, activeCartId } in module state, and
   publishes cartsSignal for cross-panel reactivity.

   Cross-panel *state* propagates via signals (see js/signals.js), not
   EventBus — EventBus stays for discrete UI events. This module owns all
   writes to cartsSignal; panels only read it. */

import { api } from '../api.js';
import { cartsSignal } from '../signals.js';
import { store } from '../store.js';

/** @type {Array<Object>} */
let _carts = [];
/** @type {string|null} */
let _activeCartId = null;
/** @type {Promise<string>|null} in-flight first-cart creation, memoized so
 *  concurrent addToActiveCart()/addBomMissing() callers before any cart
 *  exists share ONE createCart() rather than each racing to create one. */
let _ensurePromise = null;

function _publish() {
  cartsSignal.set({ carts: _carts, activeCartId: _activeCartId });
}

/** Refetch carts + active cart id from the server and publish cartsSignal. */
export async function loadCarts() {
  const result = await api('list_carts');
  if (!result) throw new Error('loadCarts: list_carts returned no data');
  _carts = result.carts || [];
  _activeCartId = result.active_cart_id ?? null;
  _publish();
  return _carts;
}

/** @returns {Array<Object>} the last-loaded carts (does not refetch). */
export function getCarts() {
  return _carts;
}

/** @returns {Object|undefined} the active cart, or undefined if none/not found. */
export function getActiveCart() {
  if (_activeCartId === null || _activeCartId === undefined) return undefined;
  return _carts.find((c) => c.id === _activeCartId);
}

/** @returns {string|null} */
export function getActiveCartId() {
  return _activeCartId;
}

/** @returns {number} total line-item count in the active cart (for the badge). */
export function cartItemCount() {
  const cart = getActiveCart();
  return cart?.items?.length ?? 0;
}

function _requireActiveCartId() {
  if (_activeCartId === null || _activeCartId === undefined) {
    throw new Error('No active cart — call setActiveCart()/createCart() first');
  }
  return _activeCartId;
}

/**
 * "<YYYY-MM-DD> · <loadedBomFileName or ''>" — trims the separator when no
 * BOM is loaded. The single shared implementation (cart-modal.js's "New"
 * button and the auto-create-on-first-add paths below all call this, rather
 * than each re-deriving their own name) — reads store.bomFileName, the same
 * getter the BOM panel itself reads/writes.
 * @returns {string}
 */
export function prefillName() {
  const today = new Date().toISOString().slice(0, 10);
  const bomName = store.bomFileName || '';
  return bomName ? `${today} · ${bomName}` : today;
}

/**
 * Ensure there is an active cart, auto-creating (and activating) one named
 * via prefillName() if none exists yet — first-use add-to-cart must not
 * silently no-op just because no cart has been created/selected yet.
 * @returns {Promise<string>} the active cart id (existing or newly created)
 */
async function _ensureActiveCartId() {
  if (_activeCartId !== null && _activeCartId !== undefined) return _activeCartId;
  if (_ensurePromise === null) {
    _ensurePromise = (async () => {
      const created = await createCart(prefillName());
      await setActiveCart(created.id);
      return created.id;
    })().finally(() => {
      _ensurePromise = null;
    });
  }
  return _ensurePromise;
}

/**
 * Add an item to the active cart, then reload carts. Auto-creates+activates
 * a cart first if none is active yet (see _ensureActiveCartId) — first-use
 * add-to-cart must work, not silently no-op.
 * @param {{partId?: string, raw?: Object|null, qty?: number, shortfall?: number, targetDistributor?: string}} opts
 */
export async function addToActiveCart({ partId, raw, qty, shortfall, targetDistributor } = {}) {
  const cartId = await _ensureActiveCartId();
  await api('add_cart_item', cartId, partId ?? null, raw ?? null, qty ?? null, targetDistributor ?? null, shortfall ?? null);
  await loadCarts();
}

/** @param {string} cartId */
export async function setActiveCart(cartId) {
  await api('set_active_cart', cartId);
  await loadCarts();
}

/** @param {string} [name] */
export async function createCart(name) {
  const created = await api('create_cart', name ?? null);
  await loadCarts();
  return created;
}

/**
 * @param {string} cartId
 * @param {string} name
 */
export async function renameCart(cartId, name) {
  await api('rename_cart', cartId, name);
  await loadCarts();
}

/** @param {string} cartId */
export async function deleteCart(cartId) {
  await api('delete_cart', cartId);
  await loadCarts();
}

/**
 * @param {string} ref
 * @param {{qty?: number, targetDistributor?: string|null, targetPackaging?: string|null,
 *          preset?: string|null, perBoardQty?: number|null}} [opts]
 * @param {string} [cartId] defaults to the active cart
 */
export async function updateItem(
  ref,
  { qty, targetDistributor, targetPackaging, preset, perBoardQty } = {},
  cartId,
) {
  const id = cartId ?? _requireActiveCartId();
  // Every field is null-means-leave-alone server-side, so a caller changing
  // one does not have to restate the others. The empty string is NOT null: it
  // is how a preset or packaging is cleared back to following the cart
  // default, which is why `preset` is passed through with ?? rather than ||.
  await api('update_cart_item', id, ref,
    qty ?? null, targetDistributor ?? null,
    targetPackaging ?? null, preset ?? null, perBoardQty ?? null);
  await loadCarts();
}

/**
 * Set how many boards the cart builds.
 *
 * Stored on the cart rather than multiplied into each line's quantity, so a
 * line stays explainable as "25 boards x 8 placements, less what is on hand"
 * instead of an unaccountable absolute number.
 * @param {number} boardCount
 * @param {string} [cartId] defaults to the active cart
 */
export async function setBoardCount(boardCount, cartId) {
  const id = cartId ?? _requireActiveCartId();
  await api('set_cart_board_count', id, boardCount);
  await loadCarts();
}

/**
 * @param {string} ref
 * @param {string} [cartId] defaults to the active cart
 */
export async function removeItem(ref, cartId) {
  const id = cartId ?? _requireActiveCartId();
  await api('remove_cart_item', id, ref);
  await loadCarts();
}

/** @param {string} [cartId] defaults to the active cart */
export async function clearCart(cartId) {
  const id = cartId ?? _requireActiveCartId();
  await api('clear_cart', id);
  await loadCarts();
}

/**
 * @param {string} distributor
 * @param {string} newName
 * @param {boolean} [removeFromSource]
 * @param {string} [cartId] defaults to the active cart
 */
export async function splitCart(distributor, newName, removeFromSource = false, cartId) {
  const id = cartId ?? _requireActiveCartId();
  const result = await api('split_cart', id, distributor, newName, removeFromSource);
  await loadCarts();
  return result;
}

/**
 * @param {string} distributor
 * @param {string} [cartId] defaults to the active cart
 */
export async function consolidateCart(distributor, cartId) {
  const id = cartId ?? _requireActiveCartId();
  const result = await api('consolidate_cart', id, distributor);
  await loadCarts();
  return result;
}

/**
 * @param {string} cartId
 * @param {string} distributor
 * @param {string} [fmt]
 */
export async function exportCart(cartId, distributor, fmt = 'csv') {
  return api('export_cart', cartId, distributor, fmt);
}

/**
 * @param {Array<Object>} missing
 * @param {string} [cartId] defaults to the active cart, auto-creating+
 *   activating one via prefillName() if none is active yet (see
 *   _ensureActiveCartId) — first-use "add missing to cart" must work, not
 *   silently no-op.
 */
export async function addBomMissing(missing, cartId) {
  const id = cartId ?? await _ensureActiveCartId();
  const result = await api('add_bom_missing_to_cart', id, missing);
  await loadCarts();
  return result;
}
