/* mfg-direct-panel.js — Direct-from-mfg import flow: state, events, API. */

import { api, AppLog, apiPurchaseOrders, apiMfgDirect } from '../../api.js';
import { showToast } from '../../ui-helpers.js';
import { scheduleInventoryRefresh, store, loadVendorsAndPOs } from '../../store.js';
import { renderEditor } from './mfg-direct-renderer.js';
import { emptyLineItem, validateLineItems } from './mfg-direct-logic.js';
import { isOcrFile } from '../import-logic.js';
import { createVendorPicker } from './vendor-picker.js';
import { openOverlay, openOverlayLoading, resolveOverlay, failOverlay } from './ocr-overlay/ocr-overlay-panel.js';
import { UndoRedo } from '../../undo-redo.js';
import { invPartKey } from '../../part-keys.js';
import { recordImportGeneration, popImportGeneration } from '../../inventory/inv-state.js';
import { openGroupingEditor } from './scan-grouping.js';
import { openScanShell, markShellTile, closeScanShell } from './scan-shell.js';
import { createScanSessionController } from './mfg-direct-scan-session.js';
import { createImportQueue } from './mfg-direct-import-queue.js';

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
function getMountEl() { return mountEl; }

// Phone-scan QR modal + push-received handling, and the sequential grouped-PO
// import queue, live in sibling modules — both operate on `state`/`mountEl`
// through this ctx rather than closing over panel internals directly.
const scanSession = createScanSessionController({
  state,
  getMountEl,
  resetForImport: _resetForImport,
  routeScanResult,
  rerender,
});
const importQueue = createImportQueue({
  state,
  getMountEl,
  resetForImport: _resetForImport,
  importPO: () => importPO(),
  cancelFlow: () => cancelFlow(),
});

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
        sourceB64: data.sourceBytes,
        sourceName: data.sourceName,
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
    const result = await apiPurchaseOrders.deleteLast();
    if (!result) throw new Error('Failed to undo PO import');
    scheduleInventoryRefresh().catch(e => AppLog.warn('inventory refresh failed: ' + e));
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
      (groupPayloads) => importQueue.startImportQueue(groupPayloads));
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
      sourceB64: only.image_b64,
      sourceName: only.filename,
    });
  AppLog.info(`Scan: overlay for ${only.filename} (${template || 'generic'})`);
}

/**
 * Unified entry for drag-drop AND click-to-browse. Either way the user gets an
 * immediate response the instant files land:
 *  - ONE image  → the in-overlay scanning skeleton that morphs into the review
 *    in place (no double-modal), via the shared openOverlayLoading/resolveOverlay.
 *  - 2+ images  → the Reading… shell (one tile per file, streamed as each OCR
 *    completes) → grouping editor (each photo its own PO by default).
 */
export async function beginScanImport(mountElement, files, template = 'generic') {
  const list = Array.isArray(files) ? files : (files ? [files] : []);
  if (!list.length) return;
  _resetForImport(mountElement, template);

  // ── Single image: in-overlay skeleton → morphs into the review in place ──
  if (list.length === 1) {
    const file = list[0];
    let b64;
    try {
      b64 = await _fileToB64(file);
    } catch {
      showToast('Could not read that file');
      return;
    }
    // Open the skeleton immediately so the dropped image is shown being "read"
    // while the (blocking) OCR call runs. The token lets resolve/fail no-op if
    // the user cancels and re-drops before this OCR call returns.
    const token = openOverlayLoading([{ b64, name: file.name }]);
    try {
      if ((await apiMfgDirect.ocrEngineAvailable()) === false) {
        failOverlay(token, 'OCR engine not available — install Tesseract');
        AppLog.warn('ocr_engine_available returned false');
        return;
      }
    } catch (exc) {
      AppLog.warn('ocr_engine_available check failed: ' + exc);
    }
    try {
      const payload = await apiMfgDirect.ocrOverlayB64(b64, file.name, template);
      if (payload && payload.pages && payload.pages.length) {
        resolveOverlay(token, payload, {
          onConfirm: (rows, vendor) => {
            state.lineItems = rows;
            state.vendor = vendor;
            state.sourceFile = { name: file.name, bytes: b64 };
            importPO();
          },
          sourceB64: b64,
          sourceName: file.name,
        });
        return;
      }
      failOverlay(token, 'No text found in that file — try a clearer photo or a CSV');
      AppLog.warn('ocr_overlay_b64 returned no pages');
    } catch (exc) {
      // The pywebview bridge surfaces Python exceptions as the JS error message;
      // TesseractMissingError text contains "Tesseract" + a winget install hint.
      const msg = String((exc && exc.message) || exc);
      if (/tesseract/i.test(msg)) {
        failOverlay(token, msg);
      } else {
        AppLog.error('OCR import failed: ' + exc);
        failOverlay(token, 'OCR failed — see log');
      }
    }
    return;
  }

  // ── Multiple images: Reading… shell (one tile per file) → grouping editor ──
  openScanShell(list.map(f => ({ name: f.name })));
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

/** Start a phone-scan session and open the QR modal — no standalone editor. */
export async function startPhoneScan(mountElement, template = 'generic') {
  _resetForImport(mountElement, template);
  const session = await apiMfgDirect.startScanSession(template);
  if (!session || !session.urls || !session.urls.length) {
    AppLog.warn('start_scan_session returned no URLs');
    showToast('Could not start scan session');
    return;
  }
  scanSession.openScanModal(session);
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
  if (scanBtn) scanBtn.onclick = scanSession.startScanSession;

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
      // `cancelled` gates the flat-parse fallback (a user cancel means stop, not
      // silently flat-parse); `token` keeps a stale OCR result from tearing down
      // a newer skeleton if the user cancels and re-drops.
      let cancelled = false;
      const token = openOverlayLoading([{ b64, name: file.name }], { onCancel: () => { cancelled = true; } });
      try {
        const payload = await apiMfgDirect.ocrOverlayB64(b64, file.name, state.scanTemplate || 'generic');
        if (cancelled) return;
        if (payload && payload.pages && payload.pages.length) {
          resolveOverlay(token, payload, {
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
      failOverlay(token);  // close the skeleton silently before the flat-parse path
      if (cancelled) return;
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

/**
 * Register the phone-scan push handlers (called once from app-init). See
 * mfg-direct-scan-session.js for the QR modal lifecycle and the
 * `scan.received`/`scan.receiving` handlers this delegates to.
 */
export function registerScanHandler() {
  scanSession.registerScanHandler();
}

function cancelFlow() {
  state.active = false;
  state.popout = false;
  scanSession.closeScanModal();
  const overlay = document.getElementById('mfg-direct-overlay');
  if (overlay) overlay.remove();
  // Re-init the regular import panel
  if (mountEl && mountEl.id === 'import-body') {
    import('../import-panel.js').then(m => m.init());
  }
}

async function importPO() {
  if (state.editingPoId) {
    // Edit path: only metadata updates (vendor/date/notes). Per-row qty/price
    // edits flow through the existing adjust/price endpoints.
    await apiPurchaseOrders.update(
      state.editingPoId, state.vendor.id, '', '');
    scheduleInventoryRefresh().catch(e => AppLog.warn('inventory refresh failed: ' + e));
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
    await apiPurchaseOrders.create(
      state.vendor.id, fileB64, fileName, '', '', items);

    // Record the import generation BEFORE the inventory re-renders, so the
    // first render after import already paints the green gutter dots. (The
    // INVENTORY_UPDATED render is what calls refreshImportMarkers; recording
    // the generation afterward leaves the dots un-rendered until some later,
    // incidental refresh.)
    const keys = state.lineItems.map(lineItemPartKey).filter(Boolean);
    recordImportGeneration(keys);

    scheduleInventoryRefresh().catch(e => AppLog.warn('inventory refresh failed: ' + e));
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
    if (importQueue.isActive()) {
      // Advance to the next PO in the grouped batch (or finish + re-init).
      importQueue.advance();
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
