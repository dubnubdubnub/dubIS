// @ts-check
/* inv-modals.js — Thin barrel for the Adjustment and Price modals.
   Split (Task 3) into adjust-modal.js + price-modal.js for focused
   maintainability; this file re-exports the public API so importers and
   the test mock target don't need to change. */

export { openAdjustModal } from './adjust-modal.js';
export { openPriceModal } from './price-modal.js';

import { initAdjustModal } from './adjust-modal.js';
import { initPriceModal } from './price-modal.js';

export function init() {
  initAdjustModal();
  initPriceModal();
}
