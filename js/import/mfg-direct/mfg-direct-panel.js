/* mfg-direct-panel.js — Direct-from-mfg import flow: state, events, API. */

import { api, AppLog, apiPurchaseOrders, apiMfgDirect } from '../../api.js';
import { showToast } from '../../ui-helpers.js';
import { onInventoryUpdated, store, loadVendorsAndPOs } from '../../store.js';
import { renderEditor, renderScanModal } from './mfg-direct-renderer.js';
import { emptyLineItem, validateLineItems,
  mapScanLineItems, scanSourceFile } from './mfg-direct-logic.js';
import { isOcrFile } from '../import-logic.js';
import { createVendorPicker } from './vendor-picker.js';
import { renderQrToCanvas } from '../../vendor/qrcode.js';
import { openOverlay } from './ocr-overlay/ocr-overlay-panel.js';
import { UndoRedo } from '../../undo-redo.js';
import { invPartKey } from '../../part-keys.js';
import { recordImportGeneration, popImportGeneration } from '../../inventory/inv-state.js';
import { openGroupingEditor } from './scan-grouping.js';
import { openScanShell, markShellTile, closeScanShell } from './scan-shell.js';

const state = {
  active: false,
  popout: false,
  editingPoId: null,
  vendor: { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' },
  sourceFile: null,  // { name, path? } once user attaches
  lineItems: [],
  scanTemplate: 'generic',  // distributor template chosen for phone scan
};

let mountEl = null;

/** Key matching inv-row-build's data-part-id, derived from a PO line item. */
function lineItemPartKey(li) {
  const dp = (li.distributor_pn || '').trim();
  const dist = (li.distributor || '').toLowerCase();
  return invPartKey({
    lcsc: /^C\d/i.test(dp) ? dp : (li.lcsc || ''),
    mpn: li.mpn || '',
    digikey: dist === 'digikey' ? dp : '',
    pololu: dist === 'pololu' ? dp : '',
    mouser: dist === 'mouser' ? dp : '',
  });
}

const vendorPicker = createVendorPicker({
  getVendor: () => state.vendor,
  setVendor: (v) => { state.vendor = v; },
  onChange: () => rerender(),
});

async function reopenReviewForUndo(data) {
  if (!data.sourceBytes) {
    showToast('Removed import (no source image to re-review)');
    return;
  }
  try {
    const payload = await apiMfgDirect.ocrOverlayB64(data.sourceBytes, data.sourceName, data.template);
    if (payload && payload.pages && payload.pages.length) {
      _resetForImport(mountEl, data.template);
      openOverlay(payload, {
        initialRows: data.rows,
        initialVendor: data.vendor,
        onConfirm: (rows, vendor) => {
          state.lineItems = rows;
          state.vendor = vendor;
          state.sourceFile = { name: data.sourceName, bytes: data.sourceBytes };
          importPO();
        },
      });
      return;
    }
  } catch (exc) {
    AppLog.warn('Undo reopen OCR failed: ' + exc);
  }
  showToast('Removed import — could not reopen review');
}

UndoRedo.register('po-import', async (action, data) => {
  if (action === 'snapshot') {
    return { _undoType: 'po-import-redo', rows: data?.rows, vendor: data?.vendor,
      template: data?.template, sourceBytes: data?.sourceBytes, sourceName: data?.sourceName };
  }
  if (data && data._undoType === 'po-import') {
    const fresh = await apiPurchaseOrders.deleteLast();
    if (!fresh) throw new Error('Failed to undo PO import');
    onInventoryUpdated(fresh);
    popImportGeneration();
    showToast(`Undid import of ${data.importedCount} rows`);
    await reopenReviewForUndo(data);
  } else if (data && data._undoType === 'po-import-redo') {
    // Redo: re-import the same rows as a fresh PO.
    state.lineItems = (data.rows || []).map(li => ({ ...li }));
    state.vendor = { ...(data.vendor || {}) };
    state.scanTemplate = data.template || 'generic';
    state.sourceFile = data.sourceBytes ? { name: data.sourceName, bytes: data.sourceBytes } : null;
    await importPO();
  }
});

// ── New two-zone entry points (image/PDF → OCR overlay; phone → scan modal) ──

/** Read a File into raw base64 (no data-URL prefix). */
function _fileToB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(typeof r.result === 'string' ? r.result.split(',')[1] : '');
    r.onerror = () => reject(new Error('read failed'));
    r.readAsDataURL(file);
  });
}

/** Reset module state for a fresh import (no editor render). */
function _resetForImport(mountElement, template) {
  mountEl = mountElement || document.getElementById('import-body');
  state.active = true;
  state.editingPoId = null;
  state.vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };
  state.lineItems = [];
  state.sourceFile = null;
  state.scanTemplate = template || 'generic';
}

/**
 * Shared downstream for every image source (drag/browse/phone). 1 photo →
 * overlay; 2+ → grouping editor. `photos[i]` is a per-photo OCR record:
 * { index, filename, image_b64, pages, prefill_rows }.
 */
export function routeScanResult(photos, groups, template, sourceHint) {
  if (!photos || !photos.length) {
    showToast('No text found — try a clearer photo or a CSV');
    return;
  }
  state.scanTemplate = template || state.scanTemplate;
  if (photos.length > 1) {
    openGroupingEditor(photos, groups, template || 'generic',
      (groupPayloads) => startImportQueue(groupPayloads));
    AppLog.info(`Scan: grouping editor for ${photos.length} photo(s)`);
    return;
  }
  const only = photos[0];
  openOverlay({ pages: only.pages, prefill_rows: only.prefill_rows, template },
    {
      onConfirm: (rows, vendor) => {
        state.lineItems = rows;
        state.vendor = vendor;
        state.sourceFile = sourceHint
          || { name: only.filename, bytes: only.image_b64 };
        importPO();
      },
    });
  AppLog.info(`Scan: overlay for ${only.filename} (${template || 'generic'})`);
}

/**
 * Unified entry for drag-drop AND click-to-browse. Opens the Reading… shell
 * IMMEDIATELY (before any OCR), OCRs each file sequentially while streaming the
 * result into its tile, then routes via routeScanResult.
 */
export async function beginScanImport(mountElement, files, template = 'generic') {
  const list = Array.isArray(files) ? files : (files ? [files] : []);
  if (!list.length) return;
  _resetForImport(mountElement, template);
  openScanShell(list.map(f => ({ name: f.name })));

  // Surface a missing OCR engine before the heavier per-file loop.
  try {
    if ((await apiMfgDirect.ocrEngineAvailable()) === false) {
      closeScanShell();
      showToast('OCR engine not available — install Tesseract');
      AppLog.warn('ocr_engine_available returned false');
      return;
    }
  } catch (exc) {
    AppLog.warn('ocr_engine_available check failed: ' + exc);
  }

  const photos = [];
  for (let i = 0; i < list.length; i++) {
    const file = list[i];
    try {
      const b64 = await _fileToB64(file);
      const payload = await apiMfgDirect.ocrOverlayB64(b64, file.name, template);
      if (payload && payload.pages && payload.pages.length) {
        photos.push({ index: i, filename: file.name, image_b64: b64,
          pages: payload.pages, prefill_rows: payload.prefill_rows || [] });
        markShellTile(i, 'done', `${(payload.prefill_rows || []).length} rows`);
      } else {
        markShellTile(i, 'error', 'No text');
      }
    } catch (exc) {
      const msg = String((exc && exc.message) || exc);
      markShellTile(i, 'error', /tesseract/i.test(msg) ? 'No OCR engine' : 'Failed');
      AppLog.error('OCR import failed: ' + exc);
    }
  }

  closeScanShell();
  if (!photos.length) {
    showToast('No text found in those files — try clearer photos or a CSV');
    return;
  }
  const groups = photos.map((_, k) => [k]);
  routeScanResult(photos, groups, template);
}

/** @deprecated single-file shim kept for callers; routes through beginScanImport. */
export async function openOcrImport(mountElement, file, template = 'generic') {
  return beginScanImport(mountElement, [file], template);
}

/** Start a phone-scan session and open the QR modal — no standalone editor. */
export async function startPhoneScan(mountElement, template = 'generic') {
  _resetForImport(mountElement, template);
  const session = await apiMfgDirect.startScanSession(template);
  if (!session || !session.urls || !session.urls.length) {
    AppLog.warn('start_scan_session returned no URLs');
    showToast('Could not start scan session');
    return;
  }
  openScanModal(session);
}

function rerender() {
  if (!mountEl) return;
  if (state.popout) {
    // Render in modal overlay
    let overlay = document.getElementById('mfg-direct-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'mfg-direct-overlay';
      overlay.className = 'modal-overlay';
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="modal modal-wide mfg-direct-modal">${renderEditor(state)}</div>`;
    overlay.classList.remove('hidden');
    bindEvents(overlay);
  } else {
    const overlay = document.getElementById('mfg-direct-overlay');
    if (overlay) overlay.remove();
    mountEl.innerHTML = renderEditor(state);
    bindEvents(mountEl);
  }
}

function bindEvents(root) {
  const popoutBtn = root.querySelector('#mfg-popout-btn');
  if (popoutBtn) popoutBtn.onclick = () => { state.popout = !state.popout; rerender(); };

  const cancelBtn = root.querySelector('#mfg-cancel');
  if (cancelBtn) cancelBtn.onclick = cancelFlow;

  const importBtn = root.querySelector('#mfg-import');
  if (importBtn) importBtn.onclick = importPO;

  const addRowBtn = root.querySelector('#mfg-add-row');
  if (addRowBtn) addRowBtn.onclick = () => { state.lineItems.push(emptyLineItem()); rerender(); };

  root.querySelectorAll('.mfg-cell').forEach(inp => {
    inp.onchange = () => {
      const idx = parseInt(inp.dataset.idx, 10);
      const field = inp.dataset.field;
      const li = state.lineItems[idx];
      li[field] = (field === 'quantity') ? parseInt(inp.value || '0', 10)
                : (field === 'unit_price') ? parseFloat(inp.value || '0')
                : inp.value;
      if (field === 'mpn' && li.mpn) {
        apiMfgDirect.matchPart(li.mpn, li.manufacturer || '').then(m => {
          li.match = m;
          rerender();
        });
      }
    };
  });

  root.querySelectorAll('.mfg-row-delete').forEach(btn => {
    btn.onclick = () => { state.lineItems.splice(parseInt(btn.dataset.idx, 10), 1); rerender(); };
  });

  root.querySelectorAll('.mfg-pseudo-chip').forEach(btn => {
    btn.onclick = () => vendorPicker.selectPseudoVendor(btn.dataset.pseudo);
  });

  const drop = root.querySelector('#mfg-source-drop');
  const fileInput = root.querySelector('#mfg-source-input');
  if (drop && fileInput) {
    drop.onclick = () => fileInput.click();
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('drag-over'); };
    drop.ondragleave = () => drop.classList.remove('drag-over');
    drop.ondrop = (e) => {
      e.preventDefault();
      drop.classList.remove('drag-over');
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) handleSourceFile(file);
    };
    fileInput.onchange = () => {
      if (fileInput.files && fileInput.files[0]) handleSourceFile(fileInput.files[0]);
    };
  }

  const replaceBtn = root.querySelector('#mfg-source-replace');
  if (replaceBtn) replaceBtn.onclick = () => { state.sourceFile = null; rerender(); };

  const scanTemplate = root.querySelector('#mfg-scan-template');
  if (scanTemplate) scanTemplate.onchange = () => { state.scanTemplate = scanTemplate.value; };

  const scanBtn = root.querySelector('#mfg-scan-btn');
  if (scanBtn) scanBtn.onclick = startScanSession;

  const nameInput = root.querySelector('#mfg-vendor-name-input');
  if (nameInput) nameInput.onblur = () => vendorPicker.onVendorNameBlur(nameInput.value);

  const urlInput = root.querySelector('#mfg-vendor-url-input');
  if (urlInput) urlInput.onblur = () => vendorPicker.onVendorUrlBlur(urlInput.value);
}

async function handleSourceFile(file) {
  const reader = new FileReader();
  reader.onload = async () => {
    // dataURL → base64 (strip "data:...;base64,")
    const dataUrl = reader.result;
    const b64 = (typeof dataUrl === 'string') ? dataUrl.split(',')[1] : '';
    state.sourceFile = { name: file.name, bytes: b64 };
    rerender();

    // Image/PDF inputs route through the OCR overlay for token-driven review.
    if (isOcrFile(file.name)) {
      try {
        const payload = await apiMfgDirect.ocrOverlayB64(b64, file.name, state.scanTemplate || 'generic');
        if (payload && payload.pages && payload.pages.length) {
          openOverlay(payload, {
            onConfirm: (rows, vendor) => {
              state.lineItems = rows;
              state.vendor = vendor;
              state.sourceFile = { name: file.name, bytes: b64 };
              importPO();
            },
          });
          return;
        }
        AppLog.warn('ocr_overlay_b64 returned no pages — falling back to flat parse');
      } catch (exc) {
        AppLog.warn('ocr_overlay_b64 failed, falling back to flat parse: ' + exc);
      }
      // fall through to the flat parse path below
    }

    try {
      const parsed = await apiMfgDirect.parseFileB64(b64, file.name, state.scanTemplate || 'generic');
      if (parsed && parsed.length) {
        state.lineItems = parsed.map(p => ({
          ...p,
          match: { status: 'pending' },
        }));
        rerender();
        // Trigger match-and-confirm for each
        await Promise.all(state.lineItems.map(async (li) => {
          if (li.mpn) li.match = await apiMfgDirect.matchPart(li.mpn, li.manufacturer);
        }));
        rerender();
      }
    } catch (exc) {
      AppLog.warn('parse failed: ' + exc);
    }
  };
  reader.readAsDataURL(file);
}

// ── Phone-scan session ────────────────────────────────────────────────
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
      const input = document.querySelector('#import-ocr-input')
        || (mountEl && mountEl.querySelector('#mfg-source-input'))
        || document.querySelector('#mfg-source-input');
      if (input) input.click();
    };
  }
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
    _resetForImport(mountEl, payload.template || 'generic');
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

/** Register the global push handlers (called once from app-init). */
export function registerScanHandler() {
  window._scanReceived = scanReceived;
  window._scanReceiving = scanReceiving;
}

function cancelFlow() {
  state.active = false;
  state.popout = false;
  closeScanModal();
  const overlay = document.getElementById('mfg-direct-overlay');
  if (overlay) overlay.remove();
  // Re-init the regular import panel
  if (mountEl && mountEl.id === 'import-body') {
    import('../import-panel.js').then(m => m.init());
  }
}

// ── Sequential multi-PO import queue ──────────────────────────────────────
// When a grouped scan yields several POs, review + import them one at a time:
// open the overlay for PO 1 → import → open PO 2 → … The overlay is a singleton,
// so the queue keeps them strictly sequential (never concurrent).
let _importQueue = null;  // { payloads: [groupPayload], idx } | null

function startImportQueue(groupPayloads) {
  if (!groupPayloads || !groupPayloads.length) return;
  _importQueue = { payloads: groupPayloads, idx: 0 };
  _openNextInQueue();
}

function _openNextInQueue() {
  if (!_importQueue || _importQueue.idx >= _importQueue.payloads.length) {
    _importQueue = null;
    cancelFlow();
    return;
  }
  const gp = _importQueue.payloads[_importQueue.idx];
  _resetForImport(mountEl, gp.template);
  state.scanTemplate = gp.template || state.scanTemplate;
  openOverlay(gp, {
    onConfirm: (rows, vendor) => {
      state.lineItems = rows;
      state.vendor = vendor;
      const src = scanSourceFile(gp);
      if (src) state.sourceFile = src;
      importPO();
    },
  });
  if (gp.poLabel) showToast(`Reviewing ${gp.poLabel}`);
}

async function importPO() {
  if (state.editingPoId) {
    // Edit path: only metadata updates (vendor/date/notes). Per-row qty/price
    // edits flow through the existing adjust/price endpoints.
    const fresh = await apiPurchaseOrders.update(
      state.editingPoId, state.vendor.id, '', '');
    onInventoryUpdated(fresh);
    showToast('PO updated');
    cancelFlow();
    return;
  }

  const errors = validateLineItems(state.lineItems);
  if (errors.length) {
    showToast(errors[0].msg);
    return;
  }
  if (!state.vendor.id) {
    showToast('Pick or enter a vendor first');
    return;
  }

  // Convert sourceFile (if any) to base64 — only when the user dropped a real file
  let fileB64 = '';
  let fileName = '';
  if (state.sourceFile && state.sourceFile.bytes) {
    fileB64 = state.sourceFile.bytes;
    fileName = state.sourceFile.name;
  }

  const items = state.lineItems.map(li => ({
    mpn: li.mpn, manufacturer: li.manufacturer, package: li.package,
    quantity: li.quantity, unit_price: li.unit_price,
    distributor: li.distributor || '',
    distributor_pn: li.distributor_pn || '',
    match: (li.match && li.match.status) || 'new',
    match_part_id: (li.match && li.match.status === 'definite') ? li.match.part_id : '',
  }));

  try {
    const fresh = await apiPurchaseOrders.create(
      state.vendor.id, fileB64, fileName, '', '', items);

    // Record the import generation BEFORE the inventory re-renders, so the
    // first render after import already paints the green gutter dots. (The
    // INVENTORY_UPDATED render is what calls refreshImportMarkers; recording
    // the generation afterward leaves the dots un-rendered until some later,
    // incidental refresh.)
    const keys = state.lineItems.map(lineItemPartKey).filter(Boolean);
    recordImportGeneration(keys);

    onInventoryUpdated(fresh);
    await loadVendorsAndPOs();

    UndoRedo.save('po-import', {
      _undoType: 'po-import',
      rows: state.lineItems.map(li => ({ ...li })),
      vendor: { ...state.vendor },
      template: state.scanTemplate || 'generic',
      sourceBytes: (state.sourceFile && state.sourceFile.bytes) || '',
      sourceName: (state.sourceFile && state.sourceFile.name) || '',
      importedCount: items.length,
    });

    showToast(`Imported ${items.length} rows from ${state.vendor.name || 'vendor'}`);
    AppLog.info(`Direct PO: ${items.length} rows from ${state.vendor.name}`);
    if (_importQueue) {
      // Advance to the next PO in the grouped batch (or finish + re-init).
      _importQueue.idx += 1;
      _openNextInQueue();
    } else {
      cancelFlow();
    }
  } catch (exc) {
    AppLog.error('Direct PO import failed: ' + exc);
  }
}

export async function editPO(poId, mountElement) {
  const result = await api('get_po_with_items', poId);
  if (!result || !result.po) return;
  mountEl = mountElement || document.getElementById('import-body');
  state.active = true;
  state.popout = false;
  state.editingPoId = poId;
  state.vendor = (store.vendors || []).find(v => v.id === result.po.vendor_id) || { id: 'v_unknown', name: 'Unknown' };
  state.sourceFile = result.po.source_file_hash
    ? { name: `archived (${result.po.source_file_ext || 'file'})`, archived: true }
    : null;
  state.lineItems = result.line_items.map(li => ({
    ...li, match: { status: 'definite' }, // existing rows by definition
  }));
  rerender();
}
