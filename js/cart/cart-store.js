// @ts-check
/* cart-store.js — Cart data layer: fetches/mutates carts via the /v1 API,
   holds the last-loaded { carts, activeCartId } in module state, and
   publishes cartsSignal for cross-panel reactivity.

   Cross-panel *state* propagates via signals (see js/signals.js), not
   EventBus — EventBus stays for discrete UI events. This module owns all
   writes to cartsSignal; panels only read it. */

import { api } from '../api.js';
import { cartsSignal } from '../signals.js';

/** @type {Array<Object>} */
let _carts = [];
/** @type {string|null} */
let _activeCartId = null;

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
 * Add an item to the active cart, then reload carts.
 * @param {{partId?: string, raw?: Object|null, qty?: number, shortfall?: number, targetDistributor?: string}} opts
 */
export async function addToActiveCart({ partId, raw, qty, shortfall, targetDistributor } = {}) {
  const cartId = _requireActiveCartId();
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
 * @param {{qty?: number, targetDistributor?: string}} [opts]
 * @param {string} [cartId] defaults to the active cart
 */
export async function updateItem(ref, { qty, targetDistributor } = {}, cartId) {
  const id = cartId ?? _requireActiveCartId();
  await api('update_cart_item', id, ref, qty ?? null, targetDistributor ?? null);
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
 * @param {string} [cartId] defaults to the active cart
 */
export async function addBomMissing(missing, cartId) {
  const id = cartId ?? _requireActiveCartId();
  const result = await api('add_bom_missing_to_cart', id, missing);
  await loadCarts();
  return result;
}
