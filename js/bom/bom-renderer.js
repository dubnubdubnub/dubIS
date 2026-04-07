/* bom/bom-renderer.js — Pure functions that return HTML strings for the BOM panel.
   No side effects, no DOM mutation, no store access. */

import { escHtml } from '../ui-helpers.js';
import { STATUS_ICONS, STATUS_ROW_CLASS, colorizeRefs, rawRowAggKey } from '../part-keys.js';

/**
 * Returns the initial BOM drop zone HTML (including results container).
 * @returns {string}
 */
export function renderDropZone() {
  return `
    <div class="drop-zone" id="bom-drop-zone">
      <p>Drop a BOM CSV here, or click to browse</p>
      <div class="hint">Supports JLCPCB, KiCad, and generic BOM formats</div>
      <input type="file" id="bom-file-input" accept=".csv,.tsv,.txt">
    </div>
    <div id="bom-results" class="hidden">
      <div class="summary" id="bom-summary"></div>
      <div class="multiplier-bar" id="bom-multiplier-bar">
        <label for="bom-qty-mult">Board qty:</label>
        <input type="number" id="bom-qty-mult" value="1" min="1" step="1">
        <button class="btn-md save-bom-btn" id="bom-save-btn" disabled>Save BOM</button>
        <button class="btn-md consume-btn" id="bom-consume-btn" disabled>Consume from inventory</button>
        <button class="btn-md clear-bom-btn" id="bom-clear-btn" disabled>Clear BOM</button>
        <span class="bom-price-info" id="bom-price-info"></span>
      </div>
      <div class="bom-staging-toolbar" id="bom-staging-toolbar">
        <h3 id="bom-staging-title">Staging</h3>
      </div>
      <div class="bom-table-wrap">
        <table>
          <thead id="bom-thead"></thead>
          <tbody id="bom-tbody"></tbody>
        </table>
      </div>
    </div>
  `;
}

/**
 * Returns the "loaded" state HTML for the drop zone.
 * @param {string} fileName
 * @returns {string}
 */
export function renderLoadedDropZone(fileName) {
  return `<p>Loaded <strong>${escHtml(fileName)}</strong> \u2014 drop or click to replace</p>
    <input type="file" id="bom-file-input" accept=".csv,.tsv,.txt" style="display:none">`;
}

/**
 * Returns summary chips HTML.
 * @param {object} counts - from countStatuses
 * @param {string} fileName
 * @param {number} multiplier
 * @returns {string}
 */
export function renderBomSummary(counts, fileName, multiplier) {
  const c = counts;
  const multLabel = multiplier > 1 ? ` (x${multiplier})` : "";
  return `
    <span class="bom-name">${escHtml(fileName)}${multLabel}</span>
    <span class="chip blue">${c.total} unique</span>
    ${c.manual > 0 ? `<span class="chip pink">${c.manual} manual</span>` : ''}
    ${c.confirmed > 0 ? `<span class="chip teal">${c.confirmed} confirmed</span>` : ''}
    <span class="chip green">${c.ok} ok</span>
    <span class="chip yellow">${c.short} short</span>
    <span class="chip orange">${c.possible} possible</span>
    <span class="chip red">${c.missing} missing</span>
    ${c.dnp > 0 ? `<span class="chip grey">${c.dnp} DNP</span>` : ''}
    ${c.covered > 0 ? `<span class="chip green">${c.covered} covered</span>` : ''}
  `;
}

/**
 * Returns price info text.
 * @param {number} pricePerBoard
 * @param {number} totalPrice
 * @param {number} multiplier
 * @returns {string}
 */
export function renderPriceInfo(pricePerBoard, totalPrice, multiplier) {
  const parts = [];
  if (pricePerBoard > 0) parts.push("$" + pricePerBoard.toFixed(2) + "/board");
  if (multiplier > 1 && totalPrice > 0) parts.push("$" + totalPrice.toFixed(2) + " total");
  return parts.join(" \u00b7 ");
}

/**
 * Returns linking banner HTML or empty string.
 * @param {{ active: boolean, invItem: object|null, bomRow: object|null }} linkingMode
 * @returns {string}
 */
export function renderLinkingBanner(linkingMode) {
  if (!linkingMode.active || (!linkingMode.invItem && !linkingMode.bomRow)) return "";
  let bannerText;
  if (linkingMode.invItem) {
    const partId = linkingMode.invItem.lcsc || linkingMode.invItem.mpn || linkingMode.invItem.description || "part";
    bannerText = `Linking: <strong>${escHtml(partId)}</strong> \u2014 click a missing, possible, or short BOM row`;
  } else {
    const bomRow = linkingMode.bomRow;
    const partId = bomRow.bom.lcsc || bomRow.bom.mpn || bomRow.bom.value || "part";
    bannerText = `Linking: <strong>${escHtml(partId)}</strong> \u2014 click an inventory part`;
  }
  return `<div class="linking-banner" id="linking-banner"><span>${bannerText}</span><button class="cancel-link-btn">Cancel</button></div>`;
}

/**
 * Returns thead HTML for the staging table.
 * @param {string[]} headers
 * @returns {string}
 */
export function renderStagingHead(headers) {
  let html = '<tr><th class="row-delete"></th><th style="width:24px"></th>';
  headers.forEach(h => { html += `<th>${escHtml(h)}</th>`; });
  html += '</tr>';
  return html;
}

/**
 * Returns a single <tr> HTML string for one staging row.
 * Uses data attributes for event delegation.
 * @param {string[]} row - raw row data
 * @param {number} ri - row index
 * @param {object} bomCols
 * @param {string[]} headers
 * @param {string|null} status - effective status for this row's agg key
 * @param {boolean} isLinkTarget - whether this row is a valid link target
 * @param {"ok"|"warn"|"dnp"|"subtotal"} classifyResult - from classifyBomRow
 * @returns {string}
 */
export function renderStagingRow(row, ri, bomCols, headers, status, isLinkTarget, classifyResult) {
  const cls = classifyResult;

  let rowClass = "";
  if (cls === "warn") rowClass = "row-warn";
  else if (cls === "subtotal") rowClass = "row-subtotal";
  else if (cls === "dnp") rowClass = "row-dnp";
  else if (status && STATUS_ROW_CLASS[status]) rowClass = STATUS_ROW_CLASS[status];

  if (isLinkTarget) rowClass += (rowClass ? " " : "") + "link-target";

  const rk = rawRowAggKey(row, bomCols);
  const dataLink = isLinkTarget && rk ? ` data-action="link" data-agg-key="${escHtml(rk)}"` : "";

  let cellsHtml = "";

  // Delete button cell
  cellsHtml += `<td class="row-delete" data-action="delete" data-ri="${ri}">\u00d7</td>`;

  // Status icon cell
  const stIcon = (cls === "ok" && status) ? (STATUS_ICONS[status] || "") : "";
  cellsHtml += `<td class="status">${stIcon}</td>`;

  // Data cells
  headers.forEach((h, ci) => {
    // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
    const val = (row[ci] != null) ? row[ci] : "";
    let lcscAttr = "";
    if (ci === bomCols.lcsc) {
      const cellVal = (val || "").trim().toUpperCase();
      if (/^C\d{4,}$/.test(cellVal)) {
        lcscAttr = ` data-lcsc="${escHtml(cellVal)}"`;
      }
    }

    let cellContent;
    if (ci === bomCols.ref) {
      // Ref cell: show colorized display with hidden input
      cellContent = `<div class="refs-cell" data-action="show-input" title="${escHtml(val)}">${colorizeRefs(val)}</div><input type="text" value="${escHtml(val)}" data-ci="${ci}" style="display:none">`;
    } else {
      cellContent = `<input type="text" value="${escHtml(val)}" data-ci="${ci}">`;
    }

    cellsHtml += `<td${lcscAttr}>${cellContent}</td>`;
  });

  return `<tr data-ri="${ri}"${dataLink} class="${rowClass}">${cellsHtml}</tr>`;
}
