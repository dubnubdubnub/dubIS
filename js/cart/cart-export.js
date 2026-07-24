// @ts-check
/* cart-export.js — Task B9: cart export UI glue.
   Consumes cartStore.exportCart(cartId, distributor, fmt) -> {content,
   unresolved, filename}. CSV format triggers a real browser download (Blob +
   object URL + a synthetic <a download> click — a legitimate user-initiated
   download from a click handler, not a bridge/file-dialog affordance). Paste
   format writes to the clipboard + toasts a confirmation. Either path warns
   (toast) about any lines the requested distributor couldn't resolve — the
   cart-modal.js top bar's Export controls are the only caller. */

import { showToast } from '../ui-helpers.js';
import { AppLog } from '../api.js';
import * as cartStore from './cart-store.js';

/**
 * Trigger a browser download of `content` as `filename` via a synthetic
 * <a download> click (Blob + object URL, revoked immediately after).
 * @param {string} filename
 * @param {string} content
 */
export function triggerDownload(filename, content) {
  const blob = new Blob([content], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * @param {Array<string>|undefined|null} unresolved refs the requested
 *   distributor couldn't resolve (no PN for that distributor).
 */
export function warnUnresolved(unresolved) {
  if (!unresolved || unresolved.length === 0) return;
  showToast(`${unresolved.length} line(s) could not be resolved for this distributor and were left out`);
}

/**
 * @param {string} cartId
 * @param {string} distributor
 */
export async function downloadCsv(cartId, distributor) {
  const { content, filename, unresolved } = await cartStore.exportCart(cartId, distributor, 'csv');
  if (!filename) throw new Error('downloadCsv: exportCart returned no filename');
  triggerDownload(filename, content);
  warnUnresolved(unresolved);
}

/**
 * @param {string} cartId
 * @param {string} distributor
 */
export async function copyPaste(cartId, distributor) {
  const { content, unresolved } = await cartStore.exportCart(cartId, distributor, 'paste');
  try {
    await navigator.clipboard.writeText(content);
  } catch (e) {
    AppLog.error('cart-export: clipboard write failed: ' + e.message);
    throw e;
  }
  showToast('Copied to clipboard');
  warnUnresolved(unresolved);
}
