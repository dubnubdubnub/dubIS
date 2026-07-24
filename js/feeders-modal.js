/* feeders-modal.js — Loading-station "Feeders" panel.

   A toolbar-launched full modal (same integration pattern as vendors-modal.js)
   over the /v1/feeders API: a DataGrid list of registered feeders with
   Load/Unload/Tag-PNG row actions, plus Register-feeder and
   download-tag-sheet form-modals.

   Direct-to-printer support is NOT built here — the .png/.pdf downloads are
   meant to be imported into Epson LabelWorks by hand. See the TODO on
   downloadTagPng()/openSheetModal() below. */

import { apiFeeders, AppLog } from './api.js';
import { showToast, Modal } from './ui-helpers.js';
import { store } from './store.js';
import { invPartKey } from './part-keys.js';
import { el } from './dom/html.js';
import { on } from './dom/delegate.js';
import { DataGrid } from './components/data-grid.js';
import { defineFormModal } from './components/form-modal.js';
import {
  describeLoadedPart, searchParts, validateRegisterForm,
  validateLoadForm, validateSheetForm, formatTapeWidth, formatLoadedQty,
} from './feeders-logic.js';

// Default tag size/DPI for the single-tag PNG download — 7mm tag targets
// 12mm label tape (see server/routes/feeders.py's DEFAULT_TAG_MM/DEFAULT_TAG_DPI).
const DEFAULT_TAG_MM = 7;
const DEFAULT_TAG_DPI = 180;

/** @type {{el:HTMLTableElement, render(data:any[]):void, refresh():void, getData():any[], destroy():void}|null} */
let grid = null;
/** @type {{open():void, close():void, el:HTMLElement}|null} */
let feedersModal = null;

// ── Backend refresh ─────────────────────────────────────────────────────────

async function refreshFeeders() {
  const list = await apiFeeders.list();
  if (grid) grid.render(list);
  return list;
}

// ── Binary downloads (LabelWorks import; direct-to-printer is future work) ──

/**
 * Fetch a binary /v1/feeders/... response and trigger a browser download.
 * Shares api.js's error-toast convention (backend {"error": "..."} body).
 */
async function downloadFile(url, filename) {
  try {
    const res = await fetch(url);
    if (!res.ok) {
      let message = res.statusText || `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error) message = body.error;
      } catch {
        // non-JSON error body — fall back to statusText
      }
      throw new Error(message);
    }
    const blob = await res.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objUrl);
    return true;
  } catch (e) {
    AppLog.error('feeders: ' + e.message);
    showToast('Error: ' + e.message);
    return false;
  }
}

// TODO(direct-to-printer): today the operator imports this PNG into Epson
// LabelWorks by hand and prints from there — see the task note in
// server/routes/feeders.py's module docstring. A future enhancement could
// drive a Windows label-printer driver directly; that needs a driver survey
// first and is deliberately out of scope here.
function downloadTagPng(tagId) {
  const url = `/v1/feeders/tags/${encodeURIComponent(tagId)}.png` +
    `?tag_mm=${DEFAULT_TAG_MM}&dpi=${DEFAULT_TAG_DPI}`;
  return downloadFile(url, `feeder-tag-${tagId}.png`);
}

function downloadTagSheet(start, count) {
  const url = `/v1/feeders/tags/sheet?start=${start}&count=${count}&tag_mm=${DEFAULT_TAG_MM}`;
  return downloadFile(url, `feeder-tags-${start}-${start + count - 1}.pdf`);
}

// ── Register-feeder form-modal ──────────────────────────────────────────────

let registerFormModal = null;

function getRegisterFormModal() {
  if (registerFormModal) return registerFormModal;
  registerFormModal = defineFormModal('feeder-register-modal', {
    title: 'Register feeder',
    subtitle: 'Bind a printed AprilTag id to a physical feeder before loading a reel onto it.',
    fields: [
      { key: 'tag_id', label: 'AprilTag id', type: 'text', placeholder: 'e.g. 0' },
      { key: 'feeder_type', label: 'Feeder type', type: 'text', placeholder: 'e.g. 8mm reel' },
    ],
    onPopulate: () => ({ tag_id: '', feeder_type: '' }),
    validate: (values) => validateRegisterForm(values),
    onConfirm: async (values) => {
      const result = await apiFeeders.register(values.tag_id.trim(), values.feeder_type.trim());
      if (result) await refreshFeeders();
      return result;
    },
    successToast: (values) => `Feeder ${values.tag_id.trim()} registered`,
    confirmLabel: 'Register',
  });
  return registerFormModal;
}

// ── Load-feeder form-modal (with an inventory part-search dropdown) ────────

let loadFormModal = null;

/** Lazily create (once) the suggestion dropdown under the part_key field. */
function ensureSuggestBox() {
  let box = document.getElementById('feeders-part-suggest');
  if (box) return box;
  const input = document.getElementById('part_key');
  const row = input && input.closest('.form-modal-row');
  if (!row) return null;
  box = el('div', { id: 'feeders-part-suggest', class: 'feeders-part-suggest hidden' });
  row.insertAdjacentElement('afterend', box);
  on(box, 'click', '[data-suggest-key]', (_e, btn) => {
    const pk = btn.dataset.suggestKey;
    const field = /** @type {HTMLInputElement} */ (document.getElementById('part_key'));
    if (field) field.value = pk;
    box.classList.add('hidden');
    box.textContent = '';
  });
  return box;
}

function renderPartSuggestions(term) {
  const box = ensureSuggestBox();
  if (!box) return;
  const matches = searchParts(store.inventory, term, 8);
  if (!matches.length) {
    box.classList.add('hidden');
    box.textContent = '';
    return;
  }
  box.textContent = '';
  for (const item of matches) {
    const pk = invPartKey(item);
    box.appendChild(el('button', {
      type: 'button', class: 'feeders-suggest-item', dataset: { suggestKey: pk },
    },
      el('span', { class: 'feeders-suggest-key' }, pk),
      el('span', { class: 'feeders-suggest-desc' }, item.description || ''),
    ));
  }
  box.classList.remove('hidden');
}

function getLoadFormModal() {
  if (loadFormModal) return loadFormModal;
  loadFormModal = defineFormModal('feeder-load-modal', {
    title: (ctx) => `Load feeder ${ctx.tagId}`,
    subtitle: (ctx) => `Feeder type: ${ctx.feederType} — tape width auto-derives from the ` +
      'part\'s package when left blank.',
    fields: [
      { key: 'part_key', label: 'Part (LCSC / MPN / description)', type: 'text',
        placeholder: 'Type to search inventory…' },
      { key: 'qty', label: 'Qty', type: 'number', attrs: { min: '0', step: '1' } },
      { key: 'tape_width_mm', label: 'Tape width (mm)', type: 'number',
        placeholder: 'auto', attrs: { min: '0', step: '0.1' } },
    ],
    onPopulate: (ctx) => ({
      part_key: ctx.loaded ? ctx.loaded.part_key : '',
      qty: ctx.loaded ? ctx.loaded.qty : '',
      tape_width_mm: (ctx.loaded && ctx.loaded.tape_width_mm !== null && ctx.loaded.tape_width_mm !== undefined)
        ? ctx.loaded.tape_width_mm : '',
    }),
    onInput: (key, values) => {
      if (key !== 'part_key') return;
      renderPartSuggestions(values.part_key);
    },
    validate: (values) => validateLoadForm(values),
    onConfirm: async (values, ctx) => {
      const partKey = values.part_key.trim();
      const qty = parseInt(values.qty, 10);
      const twStr = (values.tape_width_mm || '').trim();
      const tapeWidthMm = twStr === '' ? undefined : parseFloat(twStr);
      const result = await apiFeeders.load(ctx.tagId, partKey, qty, tapeWidthMm);
      if (result) await refreshFeeders();
      return result;
    },
    successToast: (values, ctx) => `Loaded ${values.part_key.trim()} onto feeder ${ctx.tagId}`,
    confirmLabel: 'Load',
  });
  return loadFormModal;
}

function openLoadModal(feeder) {
  const box = document.getElementById('feeders-part-suggest');
  if (box) { box.classList.add('hidden'); box.textContent = ''; }
  getLoadFormModal().open({
    tagId: feeder.tag_id,
    feederType: feeder.feeder_type,
    loaded: feeder.loaded,
  });
}

// ── Download-tag-sheet form-modal ───────────────────────────────────────────

let sheetFormModal = null;

function getSheetFormModal() {
  if (sheetFormModal) return sheetFormModal;
  sheetFormModal = defineFormModal('feeder-sheet-modal', {
    title: 'Download tag sheet (PDF)',
    subtitle: 'Print-at-100% AprilTag sheet for a normal sheet printer. For a single ' +
      'tag sized for label tape, use the row\'s "Tag PNG" button instead — both are ' +
      'meant to be imported into Epson LabelWorks, not printed directly.',
    fields: [
      { key: 'start', label: 'Start tag id', type: 'number', attrs: { min: '0', step: '1' } },
      { key: 'count', label: 'Count', type: 'number', attrs: { min: '1', step: '1' } },
    ],
    onPopulate: () => ({ start: '0', count: '24' }),
    validate: (values) => validateSheetForm(values),
    onConfirm: async (values) => {
      const start = parseInt(values.start, 10);
      const count = parseInt(values.count, 10);
      const ok = await downloadTagSheet(start, count);
      return ok ? { ok: true } : undefined;
    },
    successToast: () => 'Tag sheet downloaded',
    confirmLabel: 'Download',
  });
  return sheetFormModal;
}

// ── Unload (row action, native confirm — same pattern as vendor-flyout.js) ──

async function handleUnload(feeder) {
  if (!window.confirm(`Unload feeder ${feeder.tag_id}? This clears its bound reel.`)) return;
  const result = await apiFeeders.unload(feeder.tag_id);
  if (result === undefined) return; // api() already toasted the error
  await refreshFeeders();
  showToast(`Feeder ${feeder.tag_id} unloaded`);
  AppLog.info(`Feeder ${feeder.tag_id} unloaded`);
}

// ── DataGrid ─────────────────────────────────────────────────────────────────

function loadedPartCell(feeder) {
  const info = describeLoadedPart(feeder.loaded, store.inventory);
  if (!info) return '—';
  if (!info.description) return info.resolved ? info.part_key : `${info.part_key} (unresolved)`;
  return `${info.part_key} — ${info.description}`;
}

function buildGrid(container) {
  grid = DataGrid(container, {
    columns: [
      { key: 'tag_id', label: 'Tag', width: '70px', mono: true },
      { key: 'feeder_type', label: 'Feeder Type', width: '140px' },
      { key: '_loaded', label: 'Loaded Part', render: (feeder) => loadedPartCell(feeder) },
      { key: '_qty', label: 'Qty', width: '70px', align: 'right', mono: true,
        render: (feeder) => formatLoadedQty(feeder.loaded) },
      { key: '_tape', label: 'Tape (mm)', width: '90px', align: 'right', mono: true,
        render: (feeder) => formatTapeWidth(feeder.loaded) },
    ],
    rowKey: (feeder) => feeder.tag_id,
    rowActions: [
      { key: 'load', label: 'Load', class: 'btn-sm btn btn-cancel', title: 'Load a reel onto this feeder',
        onClick: (feeder) => openLoadModal(feeder) },
      { key: 'unload', label: 'Unload', class: 'btn-sm btn btn-danger', title: 'Clear the bound reel',
        when: (feeder) => !!feeder.loaded, onClick: (feeder) => handleUnload(feeder) },
      { key: 'tag-png', label: 'Tag PNG', class: 'btn-sm btn', title: 'Download an AprilTag PNG for LabelWorks import',
        onClick: (feeder) => downloadTagPng(feeder.tag_id) },
    ],
    emptyMessage: 'No feeders registered yet — click "Register feeder" to add one.',
    rovingNav: true,
  });
}

// ── Modal shell (built once, dynamically — no static index.html markup) ────

function buildModalDom() {
  if (document.getElementById('feeders-modal')) return;

  const tableWrap = el('div', { class: 'feeders-table-wrap', id: 'feeders-table-wrap' });
  buildGrid(tableWrap);

  const registerBtn = el('button', {
    type: 'button', class: 'btn-md btn-apply', id: 'feeders-register-btn',
  }, '+ Register feeder');
  registerBtn.addEventListener('click', () => getRegisterFormModal().open({}));

  const sheetBtn = el('button', {
    type: 'button', class: 'btn-md', id: 'feeders-print-sheet-btn',
    title: 'Download a print-at-100% sheet of AprilTags for a normal printer',
  }, 'Download tag sheet (PDF)');
  sheetBtn.addEventListener('click', () => getSheetFormModal().open({}));

  const closeBtn = el('button', {
    type: 'button', class: 'btn-md btn-cancel', id: 'feeders-close',
  }, 'Close');

  const head = el('div', { class: 'feeders-modal-head' },
    el('div', { class: 'modal-title' }, 'Feeders'),
    el('div', { class: 'feeders-modal-actions' }, registerBtn, sheetBtn, closeBtn),
  );

  const note = el('div', { class: 'feeders-modal-note' },
    'Tag images/sheets are meant for Epson LabelWorks image import, not direct printing ' +
    '— see the row\'s "Tag PNG" button and "Download tag sheet" above.',
  );

  const modalInner = el('div', { class: 'modal feeders-modal' }, head, note, tableWrap);
  const overlay = el('div', { class: 'modal-overlay hidden', id: 'feeders-modal' }, modalInner);
  document.body.appendChild(overlay);

  feedersModal = Modal('feeders-modal', { cancelId: 'feeders-close' });
}

// ── Public API ───────────────────────────────────────────────────────────────

export async function openFeedersModal() {
  buildModalDom();
  await refreshFeeders();
  feedersModal.open();
}

export function wireFeedersModal() {
  buildModalDom();
  const btn = document.getElementById('feeders-btn');
  if (btn) btn.addEventListener('click', openFeedersModal);
}
