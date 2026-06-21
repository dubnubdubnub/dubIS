// js/import/mfg-direct/scan-shell.js
/* scan-shell.js — instant "Reading…" acknowledgement shown the moment an image
 * lands (drop / browse), before any OCR runs. One tile per file; each tile flips
 * to done/error as its OCR completes. Pure DOM (no api/store). */

import { escHtml } from '../../ui-helpers.js';

function _overlay() { return document.getElementById('scan-shell-overlay'); }

export function openScanShell(items) {
  closeScanShell();
  const tiles = (items || []).map((it, i) => `
    <div class="scan-shell-tile reading" data-idx="${i}">
      <span class="scan-shell-spinner" aria-hidden="true"></span>
      <span class="scan-shell-name">${escHtml(it.name || `Image ${i + 1}`)}</span>
      <span class="scan-shell-detail">Reading…</span>
    </div>`).join('');
  const n = (items || []).length;
  const overlay = document.createElement('div');
  overlay.id = 'scan-shell-overlay';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal scan-shell-modal" role="status" aria-live="polite">
    <div class="modal-title">📸 Reading ${n} image${n === 1 ? '' : 's'}…</div>
    <div class="scan-shell-tiles">${tiles}</div>
  </div>`;
  document.body.appendChild(overlay);
}

export function markShellTile(index, status, detail) {
  const overlay = _overlay();
  if (!overlay) return;
  const tile = overlay.querySelector(`.scan-shell-tile[data-idx="${index}"]`);
  if (!tile) return;
  tile.classList.remove('reading');
  tile.classList.add(status === 'error' ? 'error' : 'done');
  const det = tile.querySelector('.scan-shell-detail');
  if (det) det.textContent = detail || (status === 'error' ? 'Failed' : 'Done');
}

export function closeScanShell() {
  const overlay = _overlay();
  if (overlay) overlay.remove();
}
