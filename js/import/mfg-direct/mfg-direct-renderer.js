/* mfg-direct-renderer.js — Pure HTML rendering for Direct import editor. */

import { escHtml, vendorIconSrc } from '../../ui-helpers.js';
import { formatMatchBadge } from './mfg-direct-logic.js';

export function renderEditor(state) {
  const { vendor, sourceFile, lineItems, popout } = state;
  const faviconHtml = vendor.icon
    ? `<span class="vendor-favicon-emoji">${escHtml(vendor.icon)}</span>`
    : (vendor.favicon_path
        ? `<img class="vendor-favicon" src="${escHtml(vendorIconSrc(vendor.favicon_path))}" alt="">`
        : `<span class="vendor-favicon-empty"></span>`);
  const isPseudo = vendor.type === 'self' || vendor.type === 'salvage' || vendor.type === 'unknown';

  return `
    <div class="mfg-direct-editor ${popout ? 'mfg-direct-popout' : ''}">
      <div class="mfg-direct-section">
        <div class="mfg-direct-label">VENDOR</div>
        <div class="mfg-direct-vendor-row">
          ${faviconHtml}
          <input type="text" class="mfg-direct-vendor-input" id="mfg-vendor-name-input"
                 data-field="name"
                 value="${escHtml(vendor.name || '')}"
                 placeholder="Vendor name (e.g. TMR Sensors, John @ Acme via email)">
          <button class="btn-sm mfg-direct-popout-btn" id="mfg-popout-btn"
                  title="${popout ? 'Collapse' : 'Expand'}">${popout ? '⬓' : '⤢'}</button>
        </div>
        ${isPseudo ? '' : `
        <div class="mfg-direct-vendor-url-row">
          <input type="text" class="mfg-direct-vendor-input" id="mfg-vendor-url-input"
                 data-field="url"
                 value="${escHtml(vendor.url || '')}"
                 placeholder="Website (optional, used to fetch favicon)">
        </div>
        `}
        <div class="mfg-direct-pseudo-row">
          <button class="btn-md filter-btn mfg-pseudo-chip" data-pseudo="v_self">⚙️ Self</button>
          <button class="btn-md filter-btn mfg-pseudo-chip" data-pseudo="v_salvage">♻️ Salvage</button>
          <button class="btn-md filter-btn mfg-pseudo-chip" data-pseudo="v_unknown">❓ Unknown</button>
        </div>
      </div>
      <div class="mfg-direct-section">
        <div class="mfg-direct-label">SOURCE FILE</div>
        ${renderSourceDrop(sourceFile)}
      </div>
      <div class="mfg-direct-section">
        <div class="mfg-direct-label">LINE ITEMS (${lineItems.length})</div>
        ${renderLineItems(lineItems)}
        <button class="btn-sm mfg-add-row-btn" id="mfg-add-row">+ Add row</button>
      </div>
      <div class="mfg-direct-actions">
        <button class="btn-md btn btn-cancel" id="mfg-cancel">✕ Cancel</button>
        <button class="btn-md btn btn-apply" id="mfg-import">Import ${lineItems.length} rows</button>
      </div>
    </div>
  `;
}

function renderSourceDrop(sourceFile) {
  if (!sourceFile) {
    return `<div class="drop-zone mfg-source-drop" id="mfg-source-drop">
      <p>drop PDF / image / CSV</p>
      <input type="file" id="mfg-source-input"
             accept=".pdf,.csv,.tsv,.xls,.xlsx,.png,.jpg,.jpeg" style="display:none">
    </div>`;
  }
  return `<div class="mfg-source-attached">
    <span class="mfg-source-icon">📄</span>
    <span class="mfg-source-name">${escHtml(sourceFile.name)}</span>
    <button class="btn-sm mfg-source-replace-btn" id="mfg-source-replace">Replace</button>
  </div>`;
}

function renderLineItems(items) {
  if (!items.length) {
    return `<div class="mfg-empty-items">No line items yet — add manually or drop a source file.</div>`;
  }
  let html = '<table class="mfg-items-table"><thead><tr>'
    + '<th>MPN</th><th>Mfg</th><th>Pkg</th><th>Qty</th><th>$/ea</th><th>Match</th><th></th>'
    + '</tr></thead><tbody>';
  items.forEach((li, i) => {
    const badge = formatMatchBadge(li.match);
    html += `<tr data-idx="${i}">
      <td><input class="mfg-cell" data-field="mpn" data-idx="${i}" value="${escHtml(li.mpn || '')}"></td>
      <td><input class="mfg-cell" data-field="manufacturer" data-idx="${i}" value="${escHtml(li.manufacturer || '')}"></td>
      <td><input class="mfg-cell" data-field="package" data-idx="${i}" value="${escHtml(li.package || '')}"></td>
      <td><input class="mfg-cell mfg-cell-num" data-field="quantity" data-idx="${i}" type="number" min="0" value="${li.quantity || 0}"></td>
      <td><input class="mfg-cell mfg-cell-num" data-field="unit_price" data-idx="${i}" type="number" min="0" step="0.01" value="${li.unit_price || 0}"></td>
      <td><button class="mfg-match-badge ${badge.cls}" data-idx="${i}">${escHtml(badge.label)}</button></td>
      <td><button class="mfg-row-delete" data-idx="${i}">×</button></td>
    </tr>`;
  });
  html += '</tbody></table>';
  return html;
}
