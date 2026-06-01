/* ocr-overlay-panel.js — controller for the OCR-overlay PO review modal.

   Owns a local `state` (from ocr-overlay-state.js) and re-renders by replacing
   the #ocr-overlay element. Mirrors the rerender()/bindEvents() pattern used in
   mfg-direct-panel.js. The vendor picker reuses the same name/url inputs +
   pseudo-vendor chips and the same apiVendors/canonicalizeUrl logic that
   mfg-direct uses, wired to a local `vendor` selection. */

import { AppLog, apiVendors } from '../../../api.js';
import { escHtml, showToast, vendorIconSrc } from '../../../ui-helpers.js';
import { store } from '../../../store.js';
import { canonicalizeUrl } from '../mfg-direct-logic.js';
import { renderModal } from './ocr-overlay-renderer.js';
import {
  createState, selectToken, selectTokens, selectCell,
  applyPending, setCellValue, setPage, clearPending,
} from './ocr-overlay-state.js';
import { normalizeRect, tokensInRect } from './ocr-overlay-hittest.js';

let state = null;
let onConfirmCb = null;
let vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };
let escHandler = null;

/** Open the overlay for a payload {pages, prefill_rows, template}. */
export function openOverlay(payload, { onConfirm } = {}) {
  state = createState(payload);
  onConfirmCb = onConfirm || null;
  vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };

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
  state = null;
  onConfirmCb = null;
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
  const isPseudo = vendor.type === 'self' || vendor.type === 'salvage' || vendor.type === 'unknown';
  const faviconHtml = vendor.icon
    ? `<span class="vendor-favicon-emoji">${escHtml(vendor.icon)}</span>`
    : (vendor.favicon_path
        ? `<img class="vendor-favicon" src="${escHtml(vendorIconSrc(vendor.favicon_path))}" alt="">`
        : `<span class="vendor-favicon-empty"></span>`);
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
  if (nameInput) nameInput.onblur = () => onVendorNameBlur(nameInput.value);
  const urlInput = mount.querySelector('#ocr-vendor-url-input');
  if (urlInput) urlInput.onblur = () => onVendorUrlBlur(urlInput.value);
  mount.querySelectorAll('.ocr-pseudo-chip').forEach(btn => {
    btn.onclick = () => selectPseudoVendor(btn.dataset.pseudo);
  });
}

function selectPseudoVendor(id) {
  const v = (store.vendors || []).find(x => x.id === id);
  if (!v) return;
  vendor = { ...v };
  rerender();
}

async function onVendorNameBlur(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return;
  if (vendor.id && vendor.name.toLowerCase() === trimmed.toLowerCase()) return;
  const pendingUrl = vendor.url || '';
  const existing = (store.vendors || []).find(
    v => v.name.toLowerCase() === trimmed.toLowerCase());
  if (existing) {
    vendor = { ...existing };
    if (pendingUrl && !vendor.url && vendor.type !== 'self'
        && vendor.type !== 'salvage' && vendor.type !== 'unknown') {
      const v = await apiVendors.upsert(vendor.id, '', pendingUrl);
      if (v) vendor = { ...v };
    }
  } else {
    const v = await apiVendors.upsert('', trimmed, pendingUrl);
    if (!v) return;
    vendor = { ...v };
  }
  rerender();
}

async function onVendorUrlBlur(text) {
  const canonical = canonicalizeUrl(text || '');
  if (!vendor.id) {
    vendor = { ...vendor, url: canonical };
    return;
  }
  if (canonical === (vendor.url || '')) return;
  if (!canonical) return;
  const v = await apiVendors.upsert(vendor.id, '', canonical);
  if (!v) return;
  vendor = { ...v };
  rerender();
}

// ── Event wiring ────────────────────────────────────────────────────────
function bindEvents(root) {
  root.querySelectorAll('.ocr-token').forEach(btn => {
    btn.onclick = () => {
      state = applyPending(selectToken(state, btn.dataset.token));
      rerender();
    };
  });

  root.querySelectorAll('.ocr-cell').forEach(td => {
    const row = parseInt(td.dataset.row, 10);
    const field = td.dataset.field;
    td.onclick = (e) => {
      if (e.detail === 2) return;  // dblclick handles editing
      state = applyPending(selectCell(state, { row, field }));
      rerender();
    };
    td.ondblclick = () => editCell(td, row, field);
  });

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
