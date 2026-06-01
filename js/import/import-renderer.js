/* import-renderer.js — Pure render functions returning HTML strings for import panel */

import { escHtml } from '../ui-helpers.js';
import { classifyRow, countWarnings } from './import-logic.js';

/**
 * Render the initial drop zone with template download buttons.
 * @param {Object} templates - PO_TEMPLATES object
 * @returns {string} HTML string
 */
export function renderDropZone(templates) {
  const ocrTemplates = {
    generic: 'Generic — direct from mfg',
    lcsc: 'LCSC',
    digikey: 'DigiKey',
    mouser: 'Mouser',
    pololu: 'Pololu',
  };
  return `
    <div class="import-section">
      <div class="import-zones">
        <div class="drop-zone import-zone-csv" id="import-drop-zone">
          <p>Drop a purchase CSV here</p>
          <div class="hint">LCSC orders, cart exports, packing lists, DigiKey, Pololu, Mouser</div>
          <input type="file" id="import-file-input" accept=".csv,.tsv,.txt,.xls">
          <div class="new-po-row" id="new-po-row">
            <span class="new-po-label">or create blank PO:</span>
            ${Object.entries(templates).map(([key, t]) =>
              `<button class="new-po-btn" data-template="${escHtml(key)}">${escHtml(t.label)}</button>`
            ).join("")}
            <button class="new-po-btn" id="import-add-row">+ add row manually</button>
          </div>
        </div>
        <div class="drop-zone import-zone-ocr" id="import-ocr-zone">
          <label class="ocr-template-label">Template:
            <select id="import-ocr-template">
              ${Object.entries(ocrTemplates).map(([key, label]) =>
                `<option value="${escHtml(key)}"${key === 'generic' ? ' selected' : ''}>${escHtml(label)}</option>`
              ).join("")}
            </select>
          </label>
          <div class="hint">Generic = a manufacturer invoice with no distributor packing list</div>
          <p>Drop an image / PDF here</p>
          <input type="file" id="import-ocr-input" accept=".png,.jpg,.jpeg,.pdf">
          <button class="new-po-btn" id="import-scan-btn">📷 Scan with phone</button>
        </div>
      </div>
      <div id="import-mapper" class="hidden"></div>
    </div>
  `;
}

/** The copyable fallback command shown when in-app install isn't possible. */
export const TESSERACT_WINGET_COMMAND = 'winget install UB-Mannheim.TesseractOCR';

/**
 * Render the missing-OCR-engine notice with an in-app Install button and a
 * copyable winget command fallback. Inserted into #import-ocr-zone when the
 * engine is unavailable.
 * @returns {string} HTML string
 */
export function renderOcrEngineNotice() {
  return `
    <div class="ocr-engine-missing" id="ocr-engine-missing">
      <p class="ocr-engine-missing-msg">Image/PDF import needs the Tesseract OCR engine.</p>
      <button class="new-po-btn" id="install-tesseract-btn">Install Tesseract</button>
      <div class="ocr-engine-missing-fallback">
        <span>or run this command yourself:</span>
        <code>${escHtml(TESSERACT_WINGET_COMMAND)}</code>
      </div>
    </div>
  `;
}

/**
 * Render the column mapping UI + staging table + import/clear buttons.
 * @param {string[]} headers - parsed CSV headers
 * @param {string[][]} rows - parsed CSV data rows
 * @param {Object<number, string>} columnMapping - source index -> target field name
 * @param {string[]} targetFields - available target field names
 * @param {string} _fileName - currently unused but available for future use
 * @returns {string} HTML string
 */
export function renderMapper(headers, rows, columnMapping, targetFields, _fileName) {
  let html = '<h3>Column Mapping</h3><div class="col-mapper">';

  headers.forEach((header, i) => {
    const current = columnMapping[i] || "Skip";
    const isMapped = current !== "Skip";
    html += `
      <div class="col-mapper-row">
        <span class="source-col" title="${escHtml(header)}">${escHtml(header)}</span>
        <span class="arrow">\u2192</span>
        <select class="col-map-select${isMapped ? ' mapped' : ''}" data-col="${i}">
          ${targetFields.map(f => `<option value="${f}"${f === current ? ' selected' : ''}>${f}</option>`).join("")}
        </select>
      </div>
    `;
  });

  html += '</div>';

  // Editable staging table
  if (rows.length > 0) {
    html += '<div class="staging-toolbar"><h3>Staging (' + rows.length + ' rows)</h3>'
          + '<button class="add-row-btn" id="add-staging-row">+ Add Row</button></div>'
          + '<div class="import-preview"><table><thead><tr>';
    html += '<th class="row-delete"></th>';
    headers.forEach((h) => {
      html += `<th><span class="th-label">${escHtml(h)}</span></th>`;
    });
    html += '</tr></thead><tbody>';
    rows.forEach((row, ri) => {
      const cls = classifyRow(row, columnMapping);
      const trClass = cls === "warn" ? " class=\"row-warn\"" : cls === "subtotal" ? " class=\"row-subtotal\"" : "";
      html += `<tr${trClass}>`;
      html += `<td class="row-delete" data-row="${ri}">\u00d7</td>`;
      row.forEach((cell, ci) => {
        html += `<td><input type="text" value="${escHtml(cell)}" data-row="${ri}" data-col="${ci}"></td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
  }

  // Import / Clear buttons
  const warns = countWarnings(rows, columnMapping);
  const warnText = warns > 0 ? " (" + warns + " warnings)" : "";
  html += `
    <div class="import-btn-row">
      <button class="clear-import-btn" id="clear-import-btn" title="Clear import">\u2715</button>
      <button class="import-btn" id="do-import-btn">
        Import ${rows.length} rows${warnText}
      </button>
    </div>
  `;

  return html;
}

/**
 * Render the staging preview table only.
 * @param {string[][]} rows
 * @param {Object<number, string>} columnMapping
 * @param {string[]} targetFields
 * @param {string} _fileName
 * @returns {string} HTML string
 */
export function renderStagingTable(rows, columnMapping, targetFields, _fileName) {
  return renderMapper([], rows, columnMapping, targetFields, _fileName);
}

/**
 * Render a template download link.
 * @param {string} label
 * @param {string[]} headers
 * @returns {string} HTML string
 */
export function renderTemplateLink(label, headers) {
  return `<button class="new-po-btn" data-headers="${escHtml(headers.join(','))}">${escHtml(label)}</button>`;
}
