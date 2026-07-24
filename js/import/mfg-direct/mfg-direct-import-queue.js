/* mfg-direct-import-queue.js — Sequential multi-PO import queue. Extracted
 * from mfg-direct-panel.js (Task 14).
 *
 * When a grouped scan yields several POs, review + import them one at a time:
 * open the overlay for PO 1 → import → open PO 2 → … The overlay is a
 * singleton, so the queue keeps them strictly sequential (never concurrent). */

import { showToast } from '../../ui-helpers.js';
import { scanSourceFile } from './mfg-direct-logic.js';
import { openOverlay } from './ocr-overlay/ocr-overlay-panel.js';

/**
 * @param {object} ctx
 * @param {object} ctx.state - the panel's shared mutable state object
 * @param {() => (HTMLElement|null)} ctx.getMountEl - current mount element
 * @param {(mountEl: HTMLElement|null, template: string) => void} ctx.resetForImport
 *   - panel's `_resetForImport`
 * @param {() => Promise<void>} ctx.importPO - panel's import-and-persist function
 * @param {() => void} ctx.cancelFlow - panel's cancel/reset function
 */
export function createImportQueue(ctx) {
  const { state, getMountEl, resetForImport, importPO, cancelFlow } = ctx;
  let _importQueue = null;  // { payloads: [groupPayload], idx } | null

  function _openNextInQueue() {
    if (!_importQueue || _importQueue.idx >= _importQueue.payloads.length) {
      _importQueue = null;
      cancelFlow();
      return;
    }
    const gp = _importQueue.payloads[_importQueue.idx];
    resetForImport(getMountEl(), gp.template);
    state.scanTemplate = gp.template || state.scanTemplate;
    openOverlay(gp, {
      onConfirm: (rows, vendor) => {
        state.lineItems = rows;
        state.vendor = vendor;
        const src = scanSourceFile(gp);
        if (src) state.sourceFile = src;
        importPO();
      },
      sourceB64: gp.image_b64,
      sourceName: gp.filename,
    });
    if (gp.poLabel) showToast(`Reviewing ${gp.poLabel}`);
  }

  function startImportQueue(groupPayloads) {
    if (!groupPayloads || !groupPayloads.length) return;
    _importQueue = { payloads: groupPayloads, idx: 0 };
    _openNextInQueue();
  }

  /** True while a grouped batch is mid-review (used by importPO's post-save branch). */
  function isActive() {
    return !!_importQueue;
  }

  /** Advance to the next PO in the batch (or finish, handled by _openNextInQueue). */
  function advance() {
    _importQueue.idx += 1;
    _openNextInQueue();
  }

  return { startImportQueue, isActive, advance };
}
