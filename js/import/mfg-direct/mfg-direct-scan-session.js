/* mfg-direct-scan-session.js — Phone-scan QR session lifecycle + the
 * backend-push handlers (`scan.received`/`scan.receiving`) that land photos
 * OCR'd on the phone back into the mfg-direct panel. Extracted from
 * mfg-direct-panel.js (Task 14); the panel owns the state object and its own
 * re-render/reset — this module only touches them through `ctx`. */

import { AppLog, apiMfgDirect } from '../../api.js';
import { showToast } from '../../ui-helpers.js';
import { renderScanModal } from './mfg-direct-renderer.js';
import { renderQrToCanvas } from '../../vendor/qrcode.js';
import { mapScanLineItems, scanSourceFile } from './mfg-direct-logic.js';
import { onEvent } from '../../sse.js';

/**
 * Wires the phone-scan QR modal and the `scan.received`/`scan.receiving`
 * push handlers around panel state owned by mfg-direct-panel.js.
 *
 * @param {object} ctx
 * @param {object} ctx.state - the panel's shared mutable state object
 *   (mutated in place, same object the panel renders from)
 * @param {() => (HTMLElement|null)} ctx.getMountEl - current mount element
 *   (the panel may reassign its own `mountEl` between calls)
 * @param {(mountEl: HTMLElement|null, template: string) => void} ctx.resetForImport
 *   - panel's `_resetForImport`
 * @param {(photos, groups, template, sourceHint?) => void} ctx.routeScanResult
 *   - panel's scan-result router (1 photo → overlay, 2+ → grouping editor)
 * @param {() => void} ctx.rerender - panel's editor re-render
 */
export function createScanSessionController(ctx) {
  const { state, getMountEl, resetForImport, routeScanResult, rerender } = ctx;

  function closeScanModal() {
    const overlay = document.getElementById('mfg-scan-overlay');
    if (overlay) overlay.remove();
  }

  function bindScanModal(root, session) {
    const canvas = root.querySelector('#mfg-scan-qr-canvas');
    const urls = session.urls || [];
    if (canvas && urls.length) {
      try {
        renderQrToCanvas(canvas, urls[0], { size: 240 });
      } catch (exc) {
        AppLog.error('QR render failed: ' + exc);
      }
    }
    // Clicking a URL re-renders the QR for that interface (lets the user pick the
    // reachable one) and copies it to the clipboard.
    root.querySelectorAll('.mfg-scan-url-btn').forEach(btn => {
      btn.onclick = () => {
        const url = btn.dataset.url;
        if (canvas && url) {
          try { renderQrToCanvas(canvas, url, { size: 240 }); }
          catch (exc) { AppLog.error('QR render failed: ' + exc); }
        }
        if (url && navigator.clipboard) {
          navigator.clipboard.writeText(url).then(
            () => showToast('Copied URL'),
            () => { /* clipboard denied — non-fatal */ });
        }
      };
    });

    const closeBtn = root.querySelector('#mfg-scan-close');
    if (closeBtn) closeBtn.onclick = closeScanModal;

    const fallbackBtn = root.querySelector('#mfg-scan-fallback');
    if (fallbackBtn) {
      fallbackBtn.onclick = () => {
        closeScanModal();
        // Return the user to a file picker. Prefer the import panel's image/PDF
        // zone input (the new two-zone entry); fall back to the legacy editor's
        // source input if the standalone editor is what's currently mounted.
        const mountEl = getMountEl();
        const input = document.querySelector('#import-ocr-input')
          || (mountEl && mountEl.querySelector('#mfg-source-input'))
          || document.querySelector('#mfg-source-input');
        if (input) input.click();
      };
    }
  }

  function openScanModal(session) {
    let overlay = document.getElementById('mfg-scan-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'mfg-scan-overlay';
      overlay.className = 'modal-overlay';
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = renderScanModal(session);
    overlay.classList.remove('hidden');
    bindScanModal(overlay, session);
  }

  async function startScanSession() {
    const template = state.scanTemplate || 'generic';
    const session = await apiMfgDirect.startScanSession(template);
    if (!session || !session.urls || !session.urls.length) {
      AppLog.warn('start_scan_session returned no URLs');
      showToast('Could not start scan session');
      return;
    }
    openScanModal(session);
  }

  /** Extract raw base64 bytes from the source the phone sent. */
  function scanSourceB64(payload) { const s = scanSourceFile(payload); return s ? s.bytes : ''; }

  /**
   * Backend → frontend push: called via evaluate_js when the phone uploads a PO
   * photo. Routes multi/single-photo payloads through routeScanResult; falls back
   * to the legacy flat-staging path for old payloads without `pages`.
   * @param {{line_items: Array, image_b64: string, filename: string, template: string, photos?: Array, pages?: Array}} payload
   */
  async function scanReceived(payload) {
    if (!payload) {
      AppLog.warn('_scanReceived called with empty payload');
      return;
    }
    // The push can arrive while the flow isn't active (e.g. modal closed, or a
    // race) — start it so the items have somewhere to land.
    if (!state.active) {
      resetForImport(getMountEl(), payload.template || 'generic');
    }
    closeScanModal();

    if (payload.photos && payload.photos.length) {
      const photos = payload.photos.map((p, i) => ({
        index: i, filename: p.filename || `scan-${i + 1}.jpg`,
        image_b64: p.image_b64 || '', pages: p.pages || [],
        prefill_rows: p.prefill_rows || [],
      }));
      routeScanResult(photos, payload.groups, payload.template || 'generic');
      return;
    }
    if (payload.pages && payload.pages.length) {
      routeScanResult(
        [{ index: 0, filename: (payload.filename || 'scan.jpg'),
           image_b64: scanSourceB64(payload), pages: payload.pages,
           prefill_rows: payload.prefill_rows || payload.line_items || [] }],
        [[0]], payload.template || 'generic', scanSourceFile(payload));
      return;
    }

    // Legacy flat-item fallback (no `pages`): land items into the staging editor.
    state.scanTemplate = payload.template || state.scanTemplate;
    state.lineItems = mapScanLineItems(payload.line_items, payload.template);
    const src = scanSourceFile(payload);
    if (src) state.sourceFile = src;
    rerender();

    // Run the existing match-and-confirm loop.
    await Promise.all(state.lineItems.map(async (li) => {
      if (li.mpn) li.match = await apiMfgDirect.matchPart(li.mpn, li.manufacturer);
    }));
    rerender();

    AppLog.info(`Scan: received ${state.lineItems.length} line items (${payload.template || 'generic'})`);
    showToast(`Scan: ${state.lineItems.length} rows received — review and import`);
  }

  /**
   * Backend → frontend push: the phone's photo has landed but OCR is still
   * running. Gives the user instant acknowledgement on the desktop instead of a
   * silent wait while OCR works. The OCR'd rows arrive shortly after via
   * window._scanReceived.
   * @param {{filename?: string, template?: string, count?: number}} payload
   */
  function scanReceiving(payload) {
    const count = (payload && payload.count) || 1;
    const noun = count > 1 ? `${count} photos` : 'Photo';
    const verb = count > 1 ? 'them' : 'it';
    // If the QR modal is still open, swap its hint to a "reading" message so the
    // feedback lands where the user is already looking.
    const hint = document.querySelector('#mfg-scan-overlay .mfg-scan-hint');
    if (hint) hint.textContent = `📸 ${noun} received — reading ${verb} now…`;
    showToast(`📸 ${noun} received — reading…`);
    const tmpl = (payload && payload.template) || '';
    AppLog.info(`Scan: ${count} photo(s) received, OCR in progress` + (tmpl ? ` (${tmpl})` : ''));
  }

  /**
   * Register the phone-scan push handlers (called once from app-init).
   * Keeps the `window._scanReceived`/`_scanReceiving` globals assigned (E2E
   * depends on calling them directly via evaluate_js) AND subscribes the same
   * functions to the equivalent SSE events (`scan.received`/`scan.receiving`)
   * published by pnp_server.py — see js/sse.js and Task 3 of
   * docs/plans/2026-07-16-phase1b-frontend-port-plan.md.
   */
  function registerScanHandler() {
    window._scanReceived = scanReceived;
    window._scanReceiving = scanReceiving;
    onEvent('scan.receiving', scanReceiving);
    onEvent('scan.received', scanReceived);
  }

  return { startScanSession, openScanModal, closeScanModal, registerScanHandler };
}
