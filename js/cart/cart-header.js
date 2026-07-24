// @ts-check
/* cart-header.js — Header cart button + live item-count badge.
   Subscribes to cartsSignal (js/signals.js) so the badge stays in sync with
   any cart mutation, from this client or another (via carts.updated SSE ->
   loadCarts(), wired in app-init.js).

   Clicking #cart-btn opens the cart modal — a stub here (openCartModal is a
   no-op placeholder) until a later task (B6) builds the real modal and
   replaces this export's implementation. */

import { loadCarts, cartItemCount } from './cart-store.js';
import { cartsSignal, effect } from '../signals.js';
import { AppLog } from '../api.js';
import * as cartAddMode from './cart-add.js';

/**
 * Open the cart modal. No-op placeholder until B6 implements the real modal —
 * kept importable/exported so B6 can swap the implementation without callers
 * (this module's click handler) needing to change.
 */
export function openCartModal() {
  // Intentionally a no-op stub — see module docblock.
}

/** Wire the header cart button + badge. Call once at startup. */
export function initCartHeader() {
  const badge = document.getElementById('cart-badge');
  const btn = document.getElementById('cart-btn');
  const addToggle = document.getElementById('cart-add-toggle');

  effect(() => {
    cartsSignal.get();
    if (badge) badge.textContent = String(cartItemCount());
  });

  if (btn) btn.addEventListener('click', () => openCartModal());
  if (addToggle) addToggle.addEventListener('click', () => cartAddMode.toggle());

  loadCarts().catch((e) => AppLog.warn('initCartHeader: loadCarts failed: ' + e.message));
}
