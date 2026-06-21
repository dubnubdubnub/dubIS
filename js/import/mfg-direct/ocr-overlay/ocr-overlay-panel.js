/* ocr-overlay-panel.js — controller for the OCR-overlay PO review modal.

   Owns a local `state` (from ocr-overlay-state.js) and re-renders by replacing
   the #ocr-overlay element. Mirrors the rerender()/bindEvents() pattern used in
   mfg-direct-panel.js. The vendor picker reuses the same name/url inputs +
   pseudo-vendor chips and the same apiVendors/canonicalizeUrl logic that
   mfg-direct uses, wired to a local `vendor` selection. */

import { AppLog } from '../../../api.js';
import { escHtml, showToast } from '../../../ui-helpers.js';
import { createVendorPicker, isPseudoVendor, vendorFaviconHtml } from '../vendor-picker.js';
import { renderModal } from './ocr-overlay-renderer.js';
import {
  createState, selectToken, selectTokens, selectCell,
  applyPending, setCellValue, setPage, clearPending, setTokenMode,
  setZoom, tokenText, addRow, deleteRow, shiftColumn, setFocusRow,
} from './ocr-overlay-state.js';
import { normalizeRect, tokensInRect } from './ocr-overlay-hittest.js';

let state = null;
let onConfirmCb = null;
let vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };
let escHandler = null;
// Active token→field drag (pointer-based). `moved` flips once the pointer travels
// past DRAG_THRESHOLD so a plain click still falls through to click-to-assign.
let drag = null;
let suppressClick = false;
const DRAG_THRESHOLD = 6;  // px before a press becomes a drag

const vendorPicker = createVendorPicker({
  getVendor: () => vendor,
  setVendor: (v) => { vendor = v; },
  onChange: () => rerender(),
});

/** Open the overlay for a payload {pages, prefill_rows, template}. */
export function openOverlay(payload, { onConfirm, initialRows, initialVendor } = {}) {
  state = createState(payload);
  if (initialRows) {
    state.rows = initialRows.map(r => ({ ...r }));
    state.lowConf = initialRows.map(() => new Set());
  }
  onConfirmCb = onConfirm || null;
  vendor = initialVendor ? { ...initialVendor }
    : { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };

  let overlay = document.getElementById('ocr-overlay');
  if (overlay) overlay.remove();
  const tmp = document.createElement('div');
  tmp.innerHTML = renderModal(state);
  overlay = tmp.firstElementChild;
  document.body.appendChild(overlay);

  escHandler = (e) => {
    if (e.key !== 'Escape') return;
    if (state.pending.kind) {
      state = clearPending(state);
      rerender();
    } else {
      closeOverlay();
    }
  };
  document.addEventListener('keydown', escHandler);

  rerender();
}

function closeOverlay() {
  const overlay = document.getElementById('ocr-overlay');
  if (overlay) overlay.remove();
  if (escHandler) {
    document.removeEventListener('keydown', escHandler);
    escHandler = null;
  }
  // Tear down any in-flight drag artifacts so a re-open starts clean.
  document.querySelectorAll('.ocr-drag-ghost').forEach(g => g.remove());
  clearDropHighlight();
  drag = null;
  suppressClick = false;
  state = null;
  onConfirmCb = null;
  vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };
}

function rerender() {
  const overlay = document.getElementById('ocr-overlay');
  if (!overlay || !state) return;
  // Replace inner modal markup; the renderer returns the full overlay wrapper,
  // so we lift its inner content into the stable #ocr-overlay container.
  const tmp = document.createElement('div');
  tmp.innerHTML = renderModal(state);
  overlay.innerHTML = tmp.firstElementChild.innerHTML;
  mountVendorPicker(overlay);
  bindEvents(overlay);
}

// ── Vendor picker (reuses mfg-direct vendor logic against a local `vendor`) ──
function mountVendorPicker(root) {
  const mount = root.querySelector('#ocr-vendor-mount');
  if (!mount) return;
  const isPseudo = isPseudoVendor(vendor);
  const faviconHtml = vendorFaviconHtml(vendor);
  mount.innerHTML = `
    <span class="ocr-vendor-picker mfg-direct-vendor-row">
      ${faviconHtml}
      <input type="text" class="mfg-direct-vendor-input" id="ocr-vendor-name-input"
             value="${escHtml(vendor.name || '')}"
             placeholder="Vendor name">
      ${isPseudo ? '' : `<input type="text" class="mfg-direct-vendor-input" id="ocr-vendor-url-input"
             value="${escHtml(vendor.url || '')}"
             placeholder="Website (optional)">`}
      <span class="mfg-direct-pseudo-row">
        <button class="btn-sm filter-btn ocr-pseudo-chip" type="button" data-pseudo="v_self">⚙️</button>
        <button class="btn-sm filter-btn ocr-pseudo-chip" type="button" data-pseudo="v_salvage">♻️</button>
        <button class="btn-sm filter-btn ocr-pseudo-chip" type="button" data-pseudo="v_unknown">❓</button>
      </span>
    </span>`;

  const nameInput = mount.querySelector('#ocr-vendor-name-input');
  if (nameInput) nameInput.onblur = () => vendorPicker.onVendorNameBlur(nameInput.value);
  const urlInput = mount.querySelector('#ocr-vendor-url-input');
  if (urlInput) urlInput.onblur = () => vendorPicker.onVendorUrlBlur(urlInput.value);
  mount.querySelectorAll('.ocr-pseudo-chip').forEach(btn => {
    btn.onclick = () => vendorPicker.selectPseudoVendor(btn.dataset.pseudo);
  });
}

// ── Event wiring ────────────────────────────────────────────────────────
function bindEvents(root) {
  root.querySelectorAll('.ocr-token').forEach(btn => {
    btn.onclick = () => {
      // A real drag already handled this press — swallow the synthetic click.
      if (suppressClick) { suppressClick = false; return; }
      state = applyPending(selectToken(state, btn.dataset.token));
      rerender();
    };
    btn.onpointerdown = (e) => onTokenPointerDown(e, btn);
    btn.onpointermove = (e) => onTokenPointerMove(e, btn);
    btn.onpointerup = (e) => onTokenPointerUp(e, btn);
  });

  const zoomRange = root.querySelector('#ocr-zoom-range');
  if (zoomRange) {
    // Update the CSS var live (smooth) without a full rerender, but persist the
    // value into state so the next rerender keeps the chosen zoom.
    zoomRange.oninput = () => {
      const z = parseFloat(zoomRange.value);
      const wrap = root.querySelector('.ocr-img-wrap');
      if (wrap) wrap.style.setProperty('--ocr-zoom', String(z));
      state = setZoom(state, z);
    };
  }

  root.querySelectorAll('.ocr-cell').forEach(td => {
    const row = parseInt(td.dataset.row, 10);
    const field = td.dataset.field;
    td.onclick = (e) => {
      if (e.detail === 2) return;  // dblclick handles editing
      state = setFocusRow(applyPending(selectCell(state, { row, field })), row);
      rerender();
    };
    td.ondblclick = () => editCell(td, row, field);
  });

  root.querySelectorAll('.ocr-row-delete[data-row]').forEach(td => {
    td.onclick = () => { state = deleteRow(state, parseInt(td.dataset.row, 10)); rerender(); };
  });
  root.querySelectorAll('.ocr-col-up').forEach(btn => {
    btn.onclick = () => { state = shiftColumn(state, btn.dataset.field, 'up'); rerender(); };
  });
  root.querySelectorAll('.ocr-col-down').forEach(btn => {
    btn.onclick = () => { state = shiftColumn(state, btn.dataset.field, 'down'); rerender(); };
  });
  const addRowBtn = root.querySelector('.ocr-add-row');
  if (addRowBtn) addRowBtn.onclick = () => { state = addRow(state); rerender(); };
  const fsBtn = root.querySelector('#ocr-fullscreen');
  if (fsBtn) fsBtn.onclick = () => { state = { ...state, fullscreen: !state.fullscreen }; rerender(); };

  const modeWords = root.querySelector('#ocr-mode-words');
  if (modeWords) modeWords.onclick = () => { state = setTokenMode(state, 'w'); rerender(); };
  const modeLines = root.querySelector('#ocr-mode-lines');
  if (modeLines) modeLines.onclick = () => { state = setTokenMode(state, 'l'); rerender(); };

  const prev = root.querySelector('#ocr-prev');
  if (prev) prev.onclick = () => { state = setPage(state, state.pageIdx - 1); rerender(); };
  const next = root.querySelector('#ocr-next');
  if (next) next.onclick = () => { state = setPage(state, state.pageIdx + 1); rerender(); };

  const cancel = root.querySelector('#ocr-cancel');
  if (cancel) cancel.onclick = closeOverlay;

  const confirm = root.querySelector('#ocr-confirm');
  if (confirm) confirm.onclick = onConfirmClick;

  bindRubberBand(root);
}

function editCell(td, row, field) {
  const current = state.rows[row] ? (state.rows[row][field] ?? '') : '';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'ocr-cell-edit';
  input.value = String(current === 0 ? '' : (current || ''));
  td.textContent = '';
  td.appendChild(input);
  input.focus();
  input.select();
  let done = false;
  const commit = () => {
    if (done) return;
    done = true;
    state = setCellValue(state, row, field, input.value);
    rerender();
  };
  input.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done = true; rerender(); }
  };
  input.onblur = commit;
}

function onConfirmClick() {
  if (!vendor.id) {
    showToast('Pick or enter a vendor first');
    return;
  }
  if (onConfirmCb) {
    try {
      onConfirmCb(state.rows, { ...vendor });
    } catch (exc) {
      AppLog.error('OCR overlay confirm failed: ' + exc);
      showToast('Import failed — see log');
    }
  }
  closeOverlay();
}

// ── Rubber-band drag selection over the scan image ──────────────────────
function bindRubberBand(root) {
  const wrap = root.querySelector('.ocr-img-wrap');
  if (!wrap) return;
  let band = null;
  let start = null;

  wrap.onpointerdown = (e) => {
    // Ignore drags that begin on a token button — those are click selections.
    if (e.target.closest('.ocr-token')) return;
    const rect = wrap.getBoundingClientRect();
    start = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    band = document.createElement('div');
    band.className = 'ocr-rubber-band';
    band.style.position = 'absolute';
    band.style.left = `${start.x}px`;
    band.style.top = `${start.y}px`;
    band.style.width = '0px';
    band.style.height = '0px';
    wrap.appendChild(band);
    wrap.setPointerCapture(e.pointerId);
  };

  wrap.onpointermove = (e) => {
    if (!band || !start) return;
    const rect = wrap.getBoundingClientRect();
    const cur = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    const r = normalizeRect(start, cur);
    band.style.left = `${r.left}px`;
    band.style.top = `${r.top}px`;
    band.style.width = `${r.right - r.left}px`;
    band.style.height = `${r.bottom - r.top}px`;
  };

  wrap.onpointerup = (e) => {
    if (!band || !start) return;
    const rect = wrap.getBoundingClientRect();
    const cur = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    const sel = normalizeRect(start, cur);
    band.remove();
    band = null;
    start = null;
    // A near-zero drag is really a click; let the click handlers take it.
    if ((sel.right - sel.left) < 4 && (sel.bottom - sel.top) < 4) return;

    const boxes = [...wrap.querySelectorAll('.ocr-token')].map(btn => {
      const b = btn.getBoundingClientRect();
      return {
        id: btn.dataset.token,
        left: b.left - rect.left, top: b.top - rect.top,
        right: b.right - rect.left, bottom: b.bottom - rect.top,
      };
    });
    const ids = tokensInRect(sel, boxes);
    if (!ids.length) return;
    state = applyPending(selectTokens(state, ids));
    rerender();
  };
}

// ── Token → field drag (drop a scanned box onto a grid cell or vendor name) ──
function onTokenPointerDown(e, btn) {
  // Left button / primary pointer only; let the rubber-band handle empty areas.
  if (e.button && e.button !== 0) return;
  // Clear any stale suppression: the click from a prior drag (if any) has already
  // fired by now, so a fresh press must start with clicks enabled again.
  suppressClick = false;
  drag = { id: btn.dataset.token, x0: e.clientX, y0: e.clientY, moved: false, ghost: null };
  try { btn.setPointerCapture(e.pointerId); } catch { /* capture optional */ }
}

function onTokenPointerMove(e, btn) {
  if (!drag || drag.id !== btn.dataset.token) return;
  if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > DRAG_THRESHOLD) {
    drag.moved = true;
    drag.ghost = makeDragGhost(tokenText(state.pages, drag.id));
    btn.classList.add('dragging');
  }
  if (drag.moved) {
    moveDragGhost(drag.ghost, e.clientX, e.clientY);
    highlightDropTarget(e.clientX, e.clientY);
  }
}

function onTokenPointerUp(e, btn) {
  if (!drag || drag.id !== btn.dataset.token) return;
  const { moved, id, ghost } = drag;
  if (ghost) ghost.remove();
  btn.classList.remove('dragging');
  clearDropHighlight();
  try { btn.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
  drag = null;
  if (!moved) return;          // it was a click — onclick handles selection
  suppressClick = true;        // a real drag — swallow the click that follows
  dropTokenAt(id, e.clientX, e.clientY);
}

/** Assign token `id`'s text to whatever droppable sits under (x, y). */
function dropTokenAt(id, x, y) {
  const target = document.elementFromPoint(x, y);
  if (!target) return;
  const cell = target.closest('.ocr-cell');
  if (cell) {
    const row = parseInt(cell.dataset.row, 10);
    const field = cell.dataset.field;
    // Reuse the tested applyPending path (combines + clears the low-conf flag).
    state = applyPending({ ...state, pending: { kind: 'source', tokenIds: [id], cell: { row, field } } });
    rerender();
    return;
  }
  if (target.closest('#ocr-vendor-name-input')) {
    // onVendorNameBlur upserts + selects the vendor, then re-renders via onChange.
    vendorPicker.onVendorNameBlur(tokenText(state.pages, id));
  }
}

function makeDragGhost(text) {
  const g = document.createElement('div');
  g.className = 'ocr-drag-ghost';
  g.textContent = text;
  document.body.appendChild(g);
  return g;
}

function moveDragGhost(ghost, x, y) {
  if (!ghost) return;
  ghost.style.left = `${x + 12}px`;
  ghost.style.top = `${y + 12}px`;
}

function highlightDropTarget(x, y) {
  clearDropHighlight();
  const el = document.elementFromPoint(x, y);
  if (!el) return;
  const target = el.closest('.ocr-cell') || el.closest('#ocr-vendor-name-input');
  if (target) target.classList.add('ocr-drop-target');
}

function clearDropHighlight() {
  document.querySelectorAll('.ocr-drop-target').forEach(el => el.classList.remove('ocr-drop-target'));
}
