/* mfg-direct-renderer.js — Pure HTML rendering for Direct import editor. */

import { escHtml, vendorIconSrc } from '../../ui-helpers.js';
import { formatMatchBadge } from './mfg-direct-logic.js';
import { PO_TEMPLATES } from '../import-logic.js';

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
        ${renderScanRow(state)}
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

/**
 * Render the QR + URL scan modal body. The QR canvas is filled in by the panel
 * after insertion (canvas drawing isn't a string operation).
 * @param {{session_id: string, template: string, urls: string[]}} session
 * @returns {string} HTML for the modal contents (caller wraps in overlay)
 */
export function renderScanModal(session) {
  const templateKey = session.template || 'generic';
  const templateLabel = (PO_TEMPLATES[templateKey] && PO_TEMPLATES[templateKey].label) || templateKey;
  const urls = session.urls || [];
  const urlList = urls.map((u, i) =>
    `<li class="mfg-scan-url${i === 0 ? ' mfg-scan-url-primary' : ''}">
      <button class="mfg-scan-url-btn" data-url="${escHtml(u)}" title="Show this URL as QR / copy">${escHtml(u)}</button>
    </li>`
  ).join('');
  return `<div class="modal mfg-scan-modal">
    <div class="modal-title">Scan a paper PO with your phone</div>
    <div class="modal-subtitle">Template: <strong>${escHtml(templateLabel)}</strong></div>
    <div class="mfg-scan-qr" id="mfg-scan-qr">
      <canvas id="mfg-scan-qr-canvas"></canvas>
    </div>
    <div class="mfg-scan-hint">Open your phone camera and point it at the code, or pick a URL below.</div>
    <ul class="mfg-scan-urls" id="mfg-scan-urls">${urlList}</ul>
    <div class="modal-actions">
      <button class="btn-md btn mfg-scan-fallback" id="mfg-scan-fallback" type="button">
        Can't connect? Choose a file instead
      </button>
      <button class="btn-md btn btn-cancel" id="mfg-scan-close" type="button">Close</button>
    </div>
  </div>`;
}

function renderScanRow(state) {
  const selected = state.scanTemplate || 'generic';
  const options = Object.entries(PO_TEMPLATES).map(([key, def]) =>
    `<option value="${escHtml(key)}"${key === selected ? ' selected' : ''}>${escHtml(def.label)}</option>`
  ).join('');
  return `<div class="mfg-scan-row">
    <select class="mfg-scan-template" id="mfg-scan-template" title="Distributor template for OCR">
      ${options}
    </select>
    <button class="btn-sm mfg-scan-btn" id="mfg-scan-btn" type="button">📱 Scan with phone</button>
  </div>`;
}

function renderLineItems(items) {
  if (!items.length) {
    return `<div class="mfg-empty-items">No line items yet — add manually or drop a source file.</div>`;
  }
  let html = '<table class="mfg-items-table"><thead><tr>'
    + '<th>MPN</th><th>Mfg</th><th>Pkg</th><th>Dist PN</th><th>Qty</th><th>$/ea</th><th>Match</th><th></th>'
    + '</tr></thead><tbody>';
  items.forEach((li, i) => {
    const badge = formatMatchBadge(li.match);
    html += `<tr data-idx="${i}">
      <td><input class="mfg-cell" data-field="mpn" data-idx="${i}" value="${escHtml(li.mpn || '')}"></td>
      <td><input class="mfg-cell" data-field="manufacturer" data-idx="${i}" value="${escHtml(li.manufacturer || '')}"></td>
      <td><input class="mfg-cell" data-field="package" data-idx="${i}" value="${escHtml(li.package || '')}"></td>
      <td><input class="mfg-cell mfg-cell-distpn" data-field="distributor_pn" data-idx="${i}" value="${escHtml(li.distributor_pn || '')}"></td>
      <td><input class="mfg-cell mfg-cell-num" data-field="quantity" data-idx="${i}" type="number" min="0" value="${li.quantity || 0}"></td>
      <td><input class="mfg-cell mfg-cell-num" data-field="unit_price" data-idx="${i}" type="number" min="0" step="0.01" value="${li.unit_price || 0}"></td>
      <td><button class="mfg-match-badge ${badge.cls}" data-idx="${i}">${escHtml(badge.label)}</button></td>
      <td><button class="mfg-row-delete" data-idx="${i}">×</button></td>
    </tr>`;
  });
  html += '</tbody></table>';
  return html;
}
