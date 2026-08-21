// @ts-check
/* cart-plan-store.js — Data layer for the active cart's purchase plan.

   Holds the last-fetched plan in module state and publishes cartPlanSignal,
   the same shape as cart-store.js does for carts. Panels read the signal;
   this module owns every write to it.

   Why the plan is fetched rather than computed here: a line's candidates
   depend on the quantity required, which depends on the board count — so
   caching offers client-side and re-ranking on each keystroke would mean
   re-implementing enumeration in JS. One batched request per cart, debounced,
   keeps the ladder walk in the one place that already gets it right
   (domain/purchase_candidates.py) at the cost of a loopback round trip.

   The plan is a RECOMMENDATION, never a write. Committing one is an ordinary
   item update through cart-store.js, so a refetch after a price refresh
   cannot rewrite a decision the user already made. */

import { api, AppLog } from '../api.js';
import { signal } from '../signals.js';
import { getBehaviorPrefs } from '../store.js';
import { getActiveCartId } from './cart-store.js';

/** @typedef {{plan: any|null, loading: boolean, error: string}} CartPlanState */

export const cartPlanSignal = signal(
  /** @type {CartPlanState} */ ({ plan: null, loading: false, error: '' }),
);

/** How long to wait after the last board-count keystroke before refetching. */
export const PLAN_DEBOUNCE_MS = 250;

/** @type {any|null} */
let _plan = null;
/** @type {string} */
let _preset = 'min';
/** @type {ReturnType<typeof setTimeout>|null} */
let _timer = null;
/** Monotonic request id: a slow earlier response must not overwrite a newer
 *  one. Dragging the board count fires several requests whose completion order
 *  is not guaranteed, and the stale winner would show a total for a board
 *  count nobody is looking at any more. */
let _seq = 0;

function _publish(loading = false, error = '') {
  cartPlanSignal.set({ plan: _plan, loading, error });
}

/** @returns {any|null} the last-loaded plan (does not refetch). */
export function getPlan() {
  return _plan;
}

/** @returns {string} the cart-wide default preset. */
export function getPreset() {
  return _preset;
}

/**
 * The reel ceiling the ranking should respect, from preferences.
 *
 * A default rule the user can change, not a constant: it decides which reel
 * the reel preset prefers and never hides one. Absent or non-numeric reads as
 * "no ceiling" rather than 0, which would reject every reel there is.
 * @returns {number|null}
 */
export function reelCeiling() {
  const raw = getBehaviorPrefs().reelCeiling;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Set the cart-wide default preset and refetch.
 * @param {string} preset
 */
export function setPreset(preset) {
  _preset = preset;
  return loadPlan();
}

/**
 * Fetch the plan for a cart. Cancels any pending debounced fetch.
 * @param {string} [cartId] defaults to the active cart
 * @returns {Promise<any|null>}
 */
export async function loadPlan(cartId) {
  if (_timer) { clearTimeout(_timer); _timer = null; }
  // Statically imported: cart-store.js does not import this module, so there
  // is no cycle to dodge — and a dynamic import here would put an
  // unpredictable number of microtasks in front of the _seq assignment,
  // making the stale-response guard below depend on module-cache warmth.
  const id = cartId ?? getActiveCartId();
  if (!id) {
    _plan = null;
    _publish();
    return null;
  }
  const seq = ++_seq;
  _publish(true);
  const result = await api('plan_cart', id, _preset, reelCeiling());
  if (seq !== _seq) return _plan;   // superseded by a newer request
  if (result === undefined) {
    // api() already logged and toasted. Keep the previous plan on screen
    // rather than blanking every price: a stale total labelled stale is more
    // use than no total at all.
    _publish(false, 'could not refresh the plan');
    return _plan;
  }
  _plan = result;
  _publish();
  return _plan;
}

/**
 * Refetch after a burst of input (the board-count stepper).
 * @param {string} [cartId]
 */
export function schedulePlanRefresh(cartId) {
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(() => {
    _timer = null;
    loadPlan(cartId).catch((e) => AppLog.error('cart-plan-store: refresh failed: ' + e.message));
  }, PLAN_DEBOUNCE_MS);
}

/** Drop the held plan (cart closed / deleted). */
export function clearPlan() {
  if (_timer) { clearTimeout(_timer); _timer = null; }
  _seq += 1;
  _plan = null;
  _publish();
}
