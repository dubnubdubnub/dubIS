// @ts-check
/* label-export-modal.js — Preview/confirmation modal for the Epson label export.

   Opens when the user clicks "Create Labels" in label-select mode (registered
   via setPreviewHandler). Previews the generated labels (editable cells),
   shows per-label estimated length with warnings, lets the user switch tape
   width, and exports one CSV per distributor to disk via save_file_dialog. */

import { api, AppLog } from './api.js';
import { showToast, Modal } from './ui-helpers.js';
import { setPreviewHandler } from './label-selection.js';
import { LABEL_EXPORT_CFG } from './constants.js';
import {
  buildLabels,
  estimateWidthMm,
  toCsvByDistributor,
} from './label-export.js';

// ── Vendor display names (vendorId → readable short suffix) ──────────────────
const VENDOR_SHORT = {
  v_lcsc:    'lcsc',
  v_digikey: 'digikey',
  v_mouser:  'mouser',
  v_pololu:  'pololu',
  v_unknown: 'unknown',
};

function vendorShort(vendorId) {
  return VENDOR_SHORT[vendorId] || String(vendorId || 'unknown').replace(/^v_/, '');
}

function vendorLabel(vendorId) {
  const s = vendorShort(vendorId);
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ── Module state ─────────────────────────────────────────────────────────────
/** @type {ReturnType<typeof Modal> | null} */
let modal = null;
/** @type {object[]} */
let currentItems = [];
/** @type {"6mm"|"12mm"} */
let currentTape = '6mm';
/** @type {object[]} */
let currentResults = [];

// ── Re-estimation (mirrors the formatters in label-export.js) ────────────────
function recompute6(result) {
  const cfg = LABEL_EXPORT_CFG;
  result.estMm = estimateWidthMm(result.text, cfg.tape6.font_pt, cfg);
  result.warnings = result.estMm > cfg.tape6.budget_mm ? ['over-budget'] : [];
}

function recompute12(result) {
  const cfg = LABEL_EXPORT_CFG;
  let estMm = 0;
  for (const line of result.columns) {
    if (line) estMm = Math.max(estMm, estimateWidthMm(line, cfg.tape12.font_pt, cfg));
  }
  result.estMm = estMm;
  if (estMm > cfg.tape12.budget_mm) result.warnings = ['over-budget'];
  else if (estMm > cfg.tape12.preferred_mm) result.warnings = ['over-preferred'];
  else result.warnings = [];
}

function recompute(result) {
  if (currentTape === '6mm') recompute6(result);
  else recompute12(result);
}

// ── Per-row mm + badge display ───────────────────────────────────────────────
function badgeFor(warnings) {
  if (warnings.includes('over-budget')) {
    return '<span class="label-badge label-badge-red">over budget</span>';
  }
  if (warnings.includes('over-preferred')) {
    return '<span class="label-badge label-badge-amber">over preferred</span>';
  }
  return '';
}

function updateRowDisplay(row, result) {
  const mmEl = row.querySelector('.label-mm');
  const badgeEl = row.querySelector('.label-badge-cell');
  if (mmEl) mmEl.textContent = result.estMm.toFixed(1) + ' mm';
  if (badgeEl) badgeEl.innerHTML = badgeFor(result.warnings);
}

// ── Render ───────────────────────────────────────────────────────────────────
function render() {
  const preview = document.getElementById('label-export-preview');
  if (!preview) return;
  preview.innerHTML = '';

  const is6mm = currentTape === '6mm';

  // Group results by vendorId, preserving first-seen order.
  /** @type {Map<string, object[]>} */
  const groups = new Map();
  for (const result of currentResults) {
    const g = groups.get(result.vendorId) || [];
    g.push(result);
    groups.set(result.vendorId, g);
  }

  for (const [vendorId, group] of groups) {
    const section = document.createElement('div');
    section.className = 'label-export-group';
    section.dataset.vendor = vendorId;

    const heading = document.createElement('div');
    heading.className = 'label-export-group-head';
    heading.textContent = vendorLabel(vendorId) + ' (' + group.length + ')';
    section.appendChild(heading);

    const table = document.createElement('table');
    table.className = 'label-export-table';

    const colHeaders = is6mm
      ? '<th>Label</th>'
      : '<th>Line 1</th><th>Line 2</th><th>Line 3</th>';
    // All strings here are static literals (no user data), so innerHTML is safe.
    table.innerHTML =
      '<thead><tr>' + colHeaders +
      '<th class="label-mm-head">Length</th><th class="label-badge-head"></th>' +
      '</tr></thead>';

    const tbody = document.createElement('tbody');
    for (const result of group) {
      const row = document.createElement('tr');
      row.className = 'label-export-row';

      if (is6mm) {
        const td = document.createElement('td');
        const cell = makeEditableCell(result.text);
        cell.dataset.field = 'text';
        td.appendChild(cell);
        row.appendChild(td);
      } else {
        for (let i = 0; i < 3; i++) {
          const td = document.createElement('td');
          const cell = makeEditableCell(result.columns[i] || '');
          cell.dataset.field = String(i);
          td.appendChild(cell);
          row.appendChild(td);
        }
      }

      const mmTd = document.createElement('td');
      mmTd.className = 'label-mm';
      row.appendChild(mmTd);

      const badgeTd = document.createElement('td');
      badgeTd.className = 'label-badge-cell';
      row.appendChild(badgeTd);

      // Wire edits for this row's cells.
      row.querySelectorAll('.label-edit-cell').forEach((node) => {
        const cell = /** @type {HTMLElement} */ (node);
        cell.addEventListener('input', () => {
          const value = cell.textContent || '';
          if (is6mm) {
            result.text = value;
            result.columns = [value];
          } else {
            const idx = Number(cell.dataset.field);
            result.columns[idx] = value;
          }
          recompute(result);
          updateRowDisplay(row, result);
        });
      });

      updateRowDisplay(row, result);
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    section.appendChild(table);
    preview.appendChild(section);
  }

  if (currentResults.length === 0) {
    preview.innerHTML = '<div class="label-export-empty">No labels to preview.</div>';
  }
}

function makeEditableCell(text) {
  const cell = document.createElement('div');
  cell.className = 'label-edit-cell';
  cell.contentEditable = 'true';
  cell.spellcheck = false;
  cell.textContent = text || '';
  return cell;
}

// ── Build the working model from stored items for the current tape ───────────
function rebuildModel() {
  currentResults = buildLabels(currentItems, currentTape, LABEL_EXPORT_CFG);
}

function setTapeRadio(tape) {
  const toggle = document.getElementById('label-export-tape-toggle');
  if (!toggle) return;
  const input = /** @type {HTMLInputElement} */ (
    toggle.querySelector('input[name="label-export-tape"][value="' + tape + '"]')
  );
  if (input) input.checked = true;
}

// ── Open handler (registered with label-selection) ───────────────────────────
function openWith(items, tape) {
  if (!Array.isArray(items) || items.length === 0) {
    showToast('No parts selected');
    AppLog.warn('Label export: no parts to preview');
    return;
  }
  currentItems = items;
  currentTape = (tape === '12mm') ? '12mm' : '6mm';
  setTapeRadio(currentTape);
  rebuildModel();
  render();
  if (modal) modal.open();
}

// ── Export ───────────────────────────────────────────────────────────────────
async function doExport() {
  const csvByVendor = toCsvByDistributor(currentResults, currentTape, LABEL_EXPORT_CFG);
  if (csvByVendor.size === 0) {
    showToast('Nothing to export');
    return;
  }

  /** @type {string[]} */
  const written = [];
  let cancelled = false;
  for (const [vendorId, csv] of csvByVendor) {
    const defaultName = 'labels_' + currentTape + '_' + vendorShort(vendorId) + '.csv';
    let result;
    try {
      result = await api('save_file_dialog', csv, defaultName);
    } catch (err) {
      AppLog.error('Label export: save_file_dialog failed for ' + vendorId + ': '
        + (err && err.message ? err.message : err));
      continue;
    }
    if (result && result.path) {
      written.push(result.path);
    } else {
      // Falsy return = the user cancelled the native Save dialog. Skip this file.
      cancelled = true;
    }
  }

  if (written.length > 0) {
    const suffix = cancelled ? ' (some cancelled)' : '';
    showToast('Exported ' + written.length + ' file' + (written.length === 1 ? '' : 's') + suffix);
    AppLog.info('Label export: wrote ' + written.length + ' file(s): ' + written.join(', '));
  } else {
    showToast('Export cancelled');
    AppLog.info('Label export: cancelled, no files written');
  }

  if (modal) modal.close();
}

// ── Footer help text ─────────────────────────────────────────────────────────
const HELP_HTML =
  'One-time Epson Label Editor setup: <strong>6&nbsp;mm</strong> template = one text frame; '
  + '<strong>12&nbsp;mm</strong> template = three stacked text frames. Place the distributor’s '
  + 'static logo in the template and save the project. '
  + 'Thereafter use <strong>Load Import Data → Overwrite Current Data</strong> to refresh from these CSVs.';

// ── Init ─────────────────────────────────────────────────────────────────────
export function init() {
  const el = document.getElementById('label-export-modal');
  if (!el) return; // not on this page

  modal = Modal('label-export-modal', { cancelId: 'label-export-cancel' });

  const helpEl = document.getElementById('label-export-help');
  if (helpEl) helpEl.innerHTML = HELP_HTML;

  const toggle = document.getElementById('label-export-tape-toggle');
  if (toggle) {
    toggle.addEventListener('change', (e) => {
      const t = /** @type {HTMLInputElement} */ (e.target);
      if (t && t.name === 'label-export-tape') {
        currentTape = (t.value === '12mm') ? '12mm' : '6mm';
        // Intentionally discards any in-progress edits — rebuilds the model
        // fresh from currentItems for the newly selected tape width.
        rebuildModel();
        render();
      }
    });
  }

  const exportBtn = document.getElementById('label-export-do');
  if (exportBtn) exportBtn.addEventListener('click', () => { doExport(); });

  setPreviewHandler(openWith);
}

// Exposed for tests.
export { openWith, doExport };
