// @ts-check
/* cart-header.js — Header cart button + live item-count badge.
   Subscribes to cartsSignal (js/signals.js) so the badge stays in sync with
   any cart mutation, from this client or another (via carts.updated SSE ->
   loadCarts(), wired in app-init.js).

   Clicking #cart-btn opens the cart modal (js/cart/cart-modal.js, Task B6) —
   re-exported here so callers that import openCartModal from this module
   don't need to change.

   Task B4: while BOM linking mode is armed with an inventory item (see
   inv-events.js's LINKING_MODE listener, which toggles #cart-btn's
   .link-target class), clicking #cart-btn instead adds the armed part to the
   active cart and exits linking mode — it does NOT open the modal in that
   case. */

import { loadCarts, cartItemCount, addToActiveCart } from './cart-store.js';
import { cartsSignal, effect } from '../signals.js';
import { AppLog } from '../api.js';
import { store } from '../store.js';
import { invPartKey } from '../part-keys.js';
import * as cartAddMode from './cart-add.js';
import { openCartModal } from './cart-modal.js';

export { openCartModal };

/** Wire the header cart button + badge. Call once at startup. */
export function initCartHeader() {
  const badge = document.getElementById('cart-badge');
  const btn = document.getElementById('cart-btn');
  const addToggle = document.getElementById('cart-add-toggle');

  effect(() => {
    cartsSignal.get();
    if (badge) badge.textContent = String(cartItemCount());
  });

  if (btn) {
    btn.addEventListener('click', () => {
      const armedItem = store.links.linkingMode ? store.links.linkingInvItem : null;
      if (armedItem) {
        addToActiveCart({ partId: invPartKey(armedItem) }).catch((e) =>
          AppLog.error('cart-header: addToActiveCart (link-target) failed: ' + e.message));
        store.links.setLinkingMode(false);
        return;
      }
      openCartModal();
    });
  }
  if (addToggle) addToggle.addEventListener('click', () => cartAddMode.toggle());

  loadCarts().catch((e) => AppLog.warn('initCartHeader: loadCarts failed: ' + e.message));
}
