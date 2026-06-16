/* ocr-overlay-renderer.js — builds the OCR overlay modal DOM (strings only).

   Pure rendering: no event listeners, no api/store imports. Event wiring lives
   in the panel (Task 6). All OCR/user text is escaped via escHtml because it
   flows straight into innerHTML.

   Token data-token ids match ocr-overlay-state.js: "<pageIdx>:<kind>:<idx>"
   where kind is "w" for words or "l" for lines. The scan pane renders tokens
   for the current state.tokenMode only (a Words/Lines toggle switches modes). */

import { escHtml } from '../../../ui-helpers.js';

export function renderModal(state) {
  if (state.loading) return renderLoadingModal(state);
  const page = state.pages[state.pageIdx];
  const selected = new Set(state.pending.tokenIds || []);
  return `<div class="modal-overlay" id="ocr-overlay">
    <div class="modal ocr-overlay-modal">
      ${renderHeader(state)}
      <div class="ocr-split">
        <div class="ocr-scan-pane">${renderScan(page, state.pageIdx, state, selected)}</div>
        <div class="ocr-grid-pane">${renderGrid(state)}</div>
      </div>
      ${renderFooter()}
    </div>
  </div>`;
}

/** Skeleton "scanning" modal shown while OCR runs (state.loading === true). */
function renderLoadingModal(state) {
  const imgs = state.images || [];
  const m = imgs.length;
  const status = m > 1 ? `Reading image ${state.imageIdx + 1} of ${m}…` : 'Reading image…';
  const first = imgs[0];
  const scanInner = (first && first.b64)
    ? `<img class="ocr-skel-img" src="data:image/png;base64,${escHtml(first.b64)}" alt="scan" draggable="false">`
    : `<div class="ocr-skel-doc" aria-hidden="true"></div>`;
  const skelRows = Array.from({ length: 4 }, () =>
    `<div class="ocr-skel-row"><span></span><span></span><span></span><span></span></div>`).join('');
  return `<div class="modal-overlay" id="ocr-overlay">
    <div class="modal ocr-overlay-modal ocr-loading" role="status" aria-live="polite" aria-busy="true">
      <div class="ocr-header">Reading scan… <span class="ocr-skel-status">${escHtml(status)}</span></div>
      <div class="ocr-split">
        <div class="ocr-scan-pane">
          <div class="ocr-skel-scan">
            ${scanInner}
            <div class="ocr-scan-sweep" aria-hidden="true"></div>
          </div>
        </div>
        <div class="ocr-grid-pane"><div class="ocr-skel-grid">${skelRows}</div></div>
      </div>
      <div class="ocr-footer">
        <span class="ocr-skel-hint">Recognizing text — this can take a few seconds.</span>
        <button id="ocr-cancel" class="btn btn-cancel" type="button">Cancel</button>
        <button id="ocr-confirm" class="btn btn-primary" type="button" disabled>Continue to import</button>
      </div>
    </div>
  </div>`;
}

function renderHeader(state) {
  const n = state.pages.length;
  const nav = n > 1
    ? `<button id="ocr-prev" type="button" ${state.pageIdx === 0 ? 'disabled' : ''}>‹</button>
       <span>Page ${state.pageIdx + 1} / ${n}</span>
       <button id="ocr-next" type="button" ${state.pageIdx === n - 1 ? 'disabled' : ''}>›</button>`
    : '';
  const wActive = state.tokenMode !== 'l';
  const modeToggle = `<span class="ocr-mode-toggle" role="group" aria-label="Token mode">
    <button id="ocr-mode-words" type="button" class="btn-sm filter-btn${wActive ? ' active' : ''}"
      aria-pressed="${wActive}">Words</button>
    <button id="ocr-mode-lines" type="button" class="btn-sm filter-btn${wActive ? '' : ' active'}"
      aria-pressed="${!wActive}">Lines</button>
  </span>`;
  const zoom = state.zoom || 1;
  const zoomCtl = `<span class="ocr-zoom" title="Zoom the scan image">
    <span class="ocr-zoom-icon" aria-hidden="true">🔍</span>
    <input id="ocr-zoom-range" type="range" min="1" max="4" step="0.25"
      value="${zoom}" aria-label="Zoom scan image">
  </span>`;
  return `<div class="ocr-header">Review scan — template: ${escHtml(state.template)} ${modeToggle} ${zoomCtl} ${nav}
    <span class="ocr-header-hint">Click or drag a box onto a cell or the vendor name.</span></div>`;
}

function renderScan(page, pageIdx, state, selected = new Set()) {
  if (!page) return '';
  const tok = (kind, arr) => (arr || []).map((t, i) => {
    const id = `${pageIdx}:${kind}:${i}`;
    const left = (t.x / page.width) * 100;
    const top = (t.y / page.height) * 100;
    const w = (t.w / page.width) * 100;
    const h = (t.h / page.height) * 100;
    const cls = selected.has(id) ? 'ocr-token selected' : 'ocr-token';
    return `<button class="${cls}" type="button" data-token="${id}" draggable="false"
      style="left:${left}%;top:${top}%;width:${w}%;height:${h}%"
      title="${escHtml(t.text)}">${escHtml(t.text)}</button>`;
  }).join('');
  const tokens = state.tokenMode === 'l' ? tok('l', page.lines) : tok('w', page.words);
  return `<div class="ocr-img-wrap" style="--ocr-zoom:${state.zoom || 1}">
    <img src="data:image/png;base64,${escHtml(page.image_b64)}" alt="scan" draggable="false">
    ${tokens}
  </div>`;
}

function renderGrid(state) {
  const fields = gridFields(state.template);
  const head = fields.map(f => `<th>${escHtml(f.label)}</th>`).join('');
  const body = state.rows.map((row, ri) => {
    const cells = fields.map(f => {
      const v = row[f.key] ?? '';
      const cls = ['ocr-cell'];
      if (state.pending.cell && state.pending.cell.row === ri && state.pending.cell.field === f.key) cls.push('target');
      if (state.lowConf[ri] && state.lowConf[ri].has(f.key)) cls.push('low-conf');
      if (v === '' || v === 0) cls.push('blank');
      return `<td class="${cls.join(' ')}" data-row="${ri}" data-field="${escHtml(f.key)}" tabindex="0">${escHtml(String(v || ''))}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return `<table class="ocr-grid"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderFooter() {
  return `<div class="ocr-footer">
    <span id="ocr-vendor-mount"></span>
    <button id="ocr-cancel" class="btn btn-cancel" type="button">Cancel</button>
    <button id="ocr-confirm" class="btn btn-primary" type="button">Continue to import</button>
  </div>`;
}

/** Grid columns for a template. Distributor templates prepend a dist-PN column. */
export function gridFields(template) {
  const distLabel = { lcsc: 'LCSC#', digikey: 'DigiKey#', mouser: 'Mouser#', pololu: 'Pololu#' }[template];
  const cols = [];
  if (distLabel) cols.push({ key: 'distributor_pn', label: distLabel });
  cols.push(
    { key: 'mpn', label: 'Mfr Part#' },
    { key: 'manufacturer', label: 'Mfr' },
    { key: 'description', label: 'Description' },
    { key: 'package', label: 'Pkg' },
    { key: 'quantity', label: 'Qty' },
    { key: 'unit_price', label: '$/ea' },
  );
  return cols;
}
