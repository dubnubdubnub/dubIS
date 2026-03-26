// @ts-check
/* inventory-renderer.js -- Pure functions that return HTML strings or DOM elements.
   No store, no events. Extracted from inventory-panel.js and bom-comparison.js. */

import { escHtml, stockValueColor } from '../ui-helpers.js';
import { invPartKey, colorizeRefs, countStatuses } from '../part-keys.js';

// ── Section header HTML ──

/**
 * Render a section header HTML string.
 * @param {string} name
 * @param {number} count
 * @param {boolean} collapsed
 * @param {boolean} isParent - true for parent section (uses inv-parent-header)
 * @returns {string}
 */
export function renderSectionHeader(name, count, collapsed, isParent) {
  var cls = isParent
    ? "inv-parent-header" + (collapsed ? " collapsed" : "")
    : "inv-section-header" + (collapsed ? " collapsed" : "");
  return '<div class="' + cls + '"><span class="chevron">\u25BE</span> ' + escHtml(name) + ' <span class="inv-section-count">(' + count + ')</span></div>';
}

/**
 * Render a subsection header HTML string.
 * @param {string} displayName
 * @param {boolean} collapsed
 * @param {number} count
 * @returns {string}
 */
export function renderSubSectionHeader(displayName, collapsed, count) {
  var cls = "inv-subsection-header" + (collapsed ? " collapsed" : "");
  return '<div class="' + cls + '"><span class="chevron">\u25BE</span> ' + escHtml(displayName) + ' <span class="inv-section-count">(' + count + ')</span></div>';
}

// ── Part row HTML ──

/**
 * Build HTML for a single inventory part row.
 * @param {Object} item - inventory item
 * @param {Object} options
 * @param {boolean} options.hideDescs - whether to hide description column
 * @param {boolean} options.isBomMode - whether BOM is active (shows link button)
 * @param {boolean} options.isLinkSource - whether this item is the linking source
 * @param {boolean} options.isReverseTarget - whether this is a reverse link target
 * @param {string} options.sectionKey - section key for threshold lookup
 * @param {number} options.threshold - stock value threshold
 * @returns {string}
 */
export function renderPartRowHtml(item, options) {
  var displayMpn = item.mpn || "";
  var displayDesc = item.description || "";

  var stockValue = item.qty * (item.unit_price || 0);
  var qtyColor = stockValueColor(stockValue, options.threshold);
  var showPriceWarn = item.qty > 0 && !(item.unit_price > 0);

  var linkBtnStr = options.isBomMode ? '<button class="link-btn' + (options.isLinkSource ? ' active' : '') + '" title="Link to missing BOM row">Link</button>' : '';
  var valueStr = stockValue > 0 ? "$" + stockValue.toFixed(2) : "\u2014";

  var partIdsHtml = '<span class="part-ids">';
  if (item.lcsc) partIdsHtml += '<span class="part-id-lcsc" data-lcsc="' + escHtml(item.lcsc) + '"><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(item.lcsc) + '</span>';
  if (item.digikey) partIdsHtml += '<span class="part-id-digikey" data-digikey="' + escHtml(item.digikey) + '"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(item.digikey) + '</span>';
  if (item.pololu) partIdsHtml += '<span class="part-id-pololu" data-pololu="' + escHtml(item.pololu) + '"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(item.pololu) + '</span>';
  if (item.mouser) partIdsHtml += '<span class="part-id-mouser" data-mouser="' + escHtml(item.mouser) + '"><img class="vendor-icon" src="data/mouser-icon.svg">' + escHtml(item.mouser) + '</span>';
  if (!item.lcsc && !item.digikey && !item.pololu && !item.mouser) partIdsHtml += '<button class="no-dist-warn" title="No distributor PN \u2014 click to add">\u26A0 NO DIST. PN</button>';
  partIdsHtml += '</span>';

  var html =
    partIdsHtml +
    '<span class="part-mpn" title="' + escHtml(displayMpn) + '">' + escHtml(displayMpn) + '</span>' +
    '<span class="part-value">' + valueStr + '</span>' +
    '<span class="part-qty" style="color:' + qtyColor + '">' + (showPriceWarn ? '<button class="price-warn-btn" title="No price data \u2014 click to set">\u26A0</button>' : '') + item.qty + '</span>' +
    (options.hideDescs ? '' : '<span class="part-desc"><span class="part-desc-inner" title="' + escHtml(displayDesc) + '">' + escHtml(displayDesc) + '</span></span>') +
    '<span class="part-actions"><button class="adj-btn" title="Adjust qty">Adjust</button>' +
    linkBtnStr + '</span>';

  return html;
}

// ── BOM comparison row element builder ──

/**
 * Build a BOM comparison row as a DOM element.
 * @param {Object} d - display data from bomRowDisplayData
 * @returns {HTMLTableRowElement}
 */
export function createBomRowElement(d) {
  var tr = document.createElement("tr");
  tr.dataset.partKey = d.partKey;
  tr.className = d.rowClass;
  if (d.isLinkingSource || d.isReverseLinkingSource) tr.classList.add("linking-source");
  if (d.isReverseTarget) tr.classList.add("link-target");

  var haveHtml = "" + d.invQty;
  if (d.altBadge) {
    var coveredCls = d.altBadge.covered ? " covered" : "";
    var expandedCls = d.altBadge.expanded ? " expanded" : "";
    haveHtml += '<br><span class="alt-badge' + coveredCls + expandedCls + '" data-part-key="' + escHtml(d.partKey) + '"><span class="chevron">\u25B8</span>+' + d.altBadge.altQty + ' (' + d.altBadge.badgeText + ')</span>';
  }

  var adjBtnHtml = d.showAdjust ? '<button class="adj-btn" title="Adjust qty">Adjust</button>' : '';
  var confirmBtnHtml = d.showConfirm
    ? '<button class="confirm-btn" title="Confirm this match">Confirm</button>'
    : d.showUnconfirm
      ? '<button class="unconfirm-btn" title="Revert to possible match">Unconfirm</button>'
      : '';
  var linkBtnHtml = d.showLink
    ? '<button class="link-btn' + (d.linkActive ? ' active' : '') + '" title="' + (d.hasInv ? 'Link to missing BOM row' : 'Link to inventory part') + '">Link</button>'
    : '';

  tr.innerHTML =
    '<td class="refs-cell" title="' + escHtml(d.refs) + '">' + colorizeRefs(d.refs) + '</td>' +
    '<td class="status">' + d.icon + '</td>' +
    '<td class="mono">' + (d.dispLcsc ? '<span' + (/^C\d{4,}$/i.test(d.dispLcsc) ? ' data-lcsc="' + escHtml(d.dispLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(d.dispLcsc) + '</span>' : '') + (d.dispLcsc && d.dispDigikey ? '<br>' : '') + (d.dispDigikey ? '<span data-digikey="' + escHtml(d.dispDigikey) + '" style="color:#ee2821"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(d.dispDigikey) + '</span>' : '') + ((d.dispLcsc || d.dispDigikey) && d.dispPololu ? '<br>' : '') + (d.dispPololu ? '<span data-pololu="' + escHtml(d.dispPololu) + '" style="color:#1e2f94"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(d.dispPololu) + '</span>' : '') + ((d.dispLcsc || d.dispDigikey || d.dispPololu) && d.dispMouser ? '<br>' : '') + (d.dispMouser ? '<span data-mouser="' + escHtml(d.dispMouser) + '" style="color:#004A99"><img class="vendor-icon" src="data/mouser-icon.svg">' + escHtml(d.dispMouser) + '</span>' : '') + '</td>' +
    '<td class="mono" title="' + escHtml(d.dispMpn) + '">' + escHtml(d.dispMpn) + '</td>' +
    '<td class="' + d.qtyClass + '" style="text-align:right;font-weight:600">' + d.effectiveQty + '</td>' +
    '<td class="inv-qty-cell ' + d.qtyClass + '" style="text-align:right;font-weight:600">' + haveHtml + '</td>' +
    '<td class="desc-cell' + (d.isMissing ? ' muted' : '') + '" title="' + escHtml(d.invDesc) + '">' + escHtml(d.invDesc) + '</td>' +
    '<td class="mono" style="text-align:center">' + d.matchLabel + '</td>' +
    '<td class="btn-group">' + confirmBtnHtml + adjBtnHtml + linkBtnHtml + '</td>';

  return tr;
}

// ── Alt rows builder ──

/**
 * Build alt inventory rows for a BOM part.
 * @param {Array<Object>} alts - alternative inventory items
 * @param {string} partKey - parent part key
 * @returns {Array<HTMLTableRowElement>}
 */
export function renderAltRows(alts, partKey) {
  var rows = [];
  for (var i = 0; i < alts.length; i++) {
    var alt = alts[i];
    var altTr = document.createElement("tr");
    altTr.className = "alt-row";
    altTr.dataset.altFor = partKey;
    altTr.dataset.invKey = invPartKey(alt);
    var altLcsc = alt.lcsc || '';
    var altDigikey = alt.digikey || '';
    var altPololu = alt.pololu || '';
    var altMouser = alt.mouser || '';
    var altPartHtml = '';
    if (altLcsc) altPartHtml += '<span' + (/^C\d{4,}$/i.test(altLcsc) ? ' data-lcsc="' + escHtml(altLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(altLcsc) + '</span>';
    if (altLcsc && altDigikey) altPartHtml += '<br>';
    if (altDigikey) altPartHtml += '<span data-digikey="' + escHtml(altDigikey) + '" style="color:#ee2821"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(altDigikey) + '</span>';
    if ((altLcsc || altDigikey) && altPololu) altPartHtml += '<br>';
    if (altPololu) altPartHtml += '<span data-pololu="' + escHtml(altPololu) + '" style="color:#1e2f94"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(altPololu) + '</span>';
    if ((altLcsc || altDigikey || altPololu) && altMouser) altPartHtml += '<br>';
    if (altMouser) altPartHtml += '<span data-mouser="' + escHtml(altMouser) + '" style="color:#004A99"><img class="vendor-icon" src="data/mouser-icon.svg">' + escHtml(altMouser) + '</span>';
    altTr.innerHTML =
      '<td></td>' +
      '<td></td>' +
      '<td class="mono">' + altPartHtml + '</td>' +
      '<td class="mono" title="' + escHtml(alt.mpn || '') + '">' + escHtml(alt.mpn || '') + '</td>' +
      '<td></td>' +
      '<td style="text-align:right;font-weight:600">' + alt.qty + '</td>' +
      '<td class="desc-cell" title="' + escHtml(alt.description) + ' ' + escHtml(alt.package) + '">' + escHtml(alt.description) + ' <span class="muted">' + escHtml(alt.package) + '</span></td>' +
      '<td></td>' +
      '<td class="btn-group"><button class="swap-btn" title="Use this alt as the selected part">Swap</button><button class="adj-btn" title="Adjust qty">Adjust</button></td>';
    rows.push(altTr);
  }
  return rows;
}

// ── Filter bar HTML ──

/**
 * Build the filter bar HTML string.
 * @param {Object} c - status counts from countStatuses
 * @param {string} activeFilter - current active filter
 * @returns {string}
 */
export function renderFilterBarHtml(c, activeFilter) {
  return '<button class="filter-btn' + (activeFilter === "all" ? " active" : "") + '" data-filter="all">All (' + c.total + ')</button>' +
    (c.manual > 0 ? '<button class="filter-btn' + (activeFilter === "manual" ? " active" : "") + '" data-filter="manual">Manual (' + c.manual + ')</button>' : '') +
    (c.confirmed > 0 ? '<button class="filter-btn' + (activeFilter === "confirmed" ? " active" : "") + '" data-filter="confirmed">Confirmed (' + c.confirmed + ')</button>' : '') +
    '<button class="filter-btn' + (activeFilter === "ok" ? " active" : "") + '" data-filter="ok">In Stock (' + c.ok + ')</button>' +
    '<button class="filter-btn' + (activeFilter === "short" ? " active" : "") + '" data-filter="short">Short (' + c.short + ')</button>' +
    '<button class="filter-btn' + (activeFilter === "possible" ? " active" : "") + '" data-filter="possible">Possible (' + c.possible + ')</button>' +
    '<button class="filter-btn' + (activeFilter === "missing" ? " active" : "") + '" data-filter="missing">Missing (' + c.missing + ')</button>' +
    (c.dnp > 0 ? '<button class="filter-btn' + (activeFilter === "dnp" ? " active" : "") + '" data-filter="dnp">DNP (' + c.dnp + ')</button>' : '');
}

// ── BOM comparison table header ──

/**
 * Returns the BOM comparison table header HTML.
 * @returns {string}
 */
export function renderBomTableHeader() {
  return '<thead><tr>' +
    '<th class="refs-col">Designators</th>' +
    '<th style="width:24px"></th>' +
    '<th style="width:110px">Part #</th>' +
    '<th style="width:140px">MPN</th>' +
    '<th style="width:50px">Need</th>' +
    '<th style="width:50px">Have</th>' +
    '<th>Description</th>' +
    '<th style="width:78px;text-align:center">Match</th>' +
    '<th class="btn-group-hdr"></th>' +
    '</tr></thead>';
}

export { countStatuses };
