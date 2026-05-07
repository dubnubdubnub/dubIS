/* mfg-direct-panel.js — Direct-from-mfg import flow: state, events, API. */

import { api, AppLog, apiVendors, apiPurchaseOrders, apiMfgDirect } from '../../api.js';
import { showToast } from '../../ui-helpers.js';
import { onInventoryUpdated, store, loadVendorsAndPOs } from '../../store.js';
import { renderEditor } from './mfg-direct-renderer.js';
import { canonicalizeUrl, emptyLineItem, validateLineItems, looksLikeUrl } from './mfg-direct-logic.js';

const state = {
  active: false,
  popout: false,
  editingPoId: null,
  vendor: { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' },
  sourceFile: null,  // { name, path? } once user attaches
  lineItems: [],
};

let mountEl = null;

export function startDirectFlow(mountElement) {
  mountEl = mountElement;
  state.active = true;
  state.popout = false;
  state.editingPoId = null;
  state.vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };
  state.sourceFile = null;
  state.lineItems = [emptyLineItem()];
  rerender();
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
      const idx = parseInt(inp.dataset.idx);
      const field = inp.dataset.field;
      const li = state.lineItems[idx];
      li[field] = (field === 'quantity') ? parseInt(inp.value || '0')
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
    btn.onclick = () => { state.lineItems.splice(parseInt(btn.dataset.idx), 1); rerender(); };
  });

  root.querySelectorAll('.mfg-pseudo-chip').forEach(btn => {
    btn.onclick = () => selectPseudoVendor(btn.dataset.pseudo);
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

  const vinp = root.querySelector('#mfg-vendor-input');
  if (vinp) {
    vinp.onblur = () => onVendorInputBlur(vinp.value);
  }
}

function selectPseudoVendor(id) {
  const v = (store.vendors || []).find(x => x.id === id);
  if (!v) return;
  state.vendor = { ...v };
  rerender();
}

async function onVendorInputBlur(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return;
  if (looksLikeUrl(trimmed)) {
    const url = canonicalizeUrl(trimmed);
    const v = await apiVendors.upsert('', '', url);
    state.vendor = { ...v };
  } else {
    // Treat as a name; if existing, pick it; else create inferred
    const existing = (store.vendors || []).find(
      v => v.name.toLowerCase() === trimmed.toLowerCase());
    if (existing) {
      state.vendor = { ...existing };
    } else {
      const v = await apiVendors.upsert('', trimmed, '');
      state.vendor = { ...v };
    }
  }
  rerender();
}

async function handleSourceFile(file) {
  const reader = new FileReader();
  reader.onload = async () => {
    // dataURL → base64 (strip "data:...;base64,")
    const dataUrl = reader.result;
    const b64 = (typeof dataUrl === 'string') ? dataUrl.split(',')[1] : '';
    state.sourceFile = { name: file.name, bytes: b64 };
    rerender();
    try {
      const parsed = await apiMfgDirect.parseFileB64(b64, file.name);
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

function cancelFlow() {
  state.active = false;
  state.popout = false;
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
    match: (li.match && li.match.status) || 'new',
    match_part_id: (li.match && li.match.status === 'definite') ? li.match.part_id : '',
  }));

  try {
    const fresh = await apiPurchaseOrders.create(
      state.vendor.id, fileB64, fileName, '', '', items);
    onInventoryUpdated(fresh);
    await loadVendorsAndPOs();
    showToast(`Imported ${items.length} rows from ${state.vendor.name || 'vendor'}`);
    AppLog.info(`Direct PO: ${items.length} rows from ${state.vendor.name}`);
    cancelFlow();
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
