// @ts-check
/* inv-html-builders.js -- Pure functions that return HTML strings or DOM elements.
   No store, no events. Extracted from inventory-panel.js and bom-comparison.js. */

import { escHtml, stockValueColor, formatMoney } from '../ui-helpers.js';
import { invPartKey, colorizeRefs, countStatuses } from '../part-keys.js';
import { renderFanStack } from './favicon-stack.js';
import { isLabelMode, isSelected } from '../label-selection.js';
/* col-resize-LOGIC, not col-resize.js: this module must stay store-free (see
   the header note), and the logic module imports nothing at all. */
import { colResizeHandleHtml } from '../col-resize-logic.js';

/**
 * Build the label-mode selection checkbox HTML for a given part key.
 * Rendered in place of the row's right-edge action buttons when label mode is on.
 * @param {string} key - invPartKey(item)
 * @returns {string}
 */
function labelCheckboxHtml(key) {
  var checked = isSelected(key) ? ' checked' : '';
  return '<input type="checkbox" class="label-select-checkbox" data-key="' +
    escHtml(key) + '"' + checked + ' title="Select for label export">';
}

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
 * @param {import('../types.js').InventoryItem} item - inventory item
 * @param {Object} options
 * @param {boolean} options.hideDescs - whether to hide description column
 * @param {boolean} options.isBomMode - whether BOM is active (shows link button)
 * @param {boolean} options.isLinkSource - whether this item is the linking source
 * @param {boolean} options.isReverseTarget - whether this is a reverse link target
 * @param {string} options.sectionKey - section key for threshold lookup
 * @param {number} options.threshold - stock value threshold
 * @param {string} [options.sectionChip] - optional section name shown as a chip in flat mode
 * @param {number} [options.importOpacity] - >0 marks a recently-imported part (mirrors the scrollbar gutter dot's per-generation fade)
 * @returns {string}
 */
/**
 * Find the generic part group that a given part belongs to.
 * @param {string} partKey
 * @param {Array} genericParts
 * @returns {{ generic_part_id: string, name: string } | null}
 */
function findGenericGroup(partKey, genericParts) {
  if (!genericParts || !partKey) return null;
  var pk = partKey.toUpperCase();
  for (var i = 0; i < genericParts.length; i++) {
    var gp = genericParts[i];
    if (!gp.members) continue;
    for (var j = 0; j < gp.members.length; j++) {
      if (gp.members[j].part_id.toUpperCase() === pk) return gp;
    }
  }
  return null;
}

export function renderPartRowHtml(item, options) {
  var displayMpn = item.mpn || "";
  var displayDesc = item.description || "";

  var nearMissBadgeHtml = '';
  if (options.nearMiss) {
    var nm = options.nearMiss;
    var tip = 'Value matches ' + (nm.bomRefs || 'BOM row') +
              ' (' + (nm.bomValue || 'value') + ')' +
              ' but footprint mismatch: inventory is ' + (nm.invPackage || '?') +
              ', BOM wants ' + (nm.bomFootprintCode || '?') +
              '. Click Link to override.';
    nearMissBadgeHtml = '<button class="near-miss-badge" title="' + escHtml(tip) + '">⚠</button>';
  }

  var stockValue = item.qty * (item.unit_price || 0);
  var qtyColor = stockValueColor(stockValue, options.threshold);
  var showPriceWarn = item.qty > 0 && !(item.unit_price > 0);

  var linkBtnStr = options.isBomMode ? '<button class="btn-sm link-btn' + (options.isLinkSource ? ' active' : '') + '" title="Link to missing BOM row">Link</button>' : '';
  var groupBtnStr = '';
  if (options.genericParts) {
    var gp = findGenericGroup(invPartKey(item), options.genericParts);
    if (gp) {
      groupBtnStr = '<button class="generic-group-badge" data-generic-id="' + escHtml(gp.generic_part_id) + '" title="' + escHtml(gp.name) + '">\u25C6 ' + escHtml(gp.name) + '</button>';
    }
  }
  var valueStr = stockValue > 0 ? formatMoney(stockValue) : "\u2014";

  var unitPrice = Number(item.unit_price) || 0;
  var unitPriceStr;
  if (unitPrice >= 0.01) unitPriceStr = formatMoney(unitPrice);
  else if (unitPrice > 0) unitPriceStr = '$' + unitPrice.toFixed(4);
  else unitPriceStr = '\u2014';

  var sectionChipHtml = options.sectionChip
    ? '<span class="inv-section-chip">' + escHtml(options.sectionChip) + '</span>'
    : '';

  var partIdsHtml = '<span class="part-ids">';
  if (item.lcsc) partIdsHtml += '<span class="part-id-lcsc" data-lcsc="' + escHtml(item.lcsc) + '"><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(item.lcsc) + '</span>';
  if (item.digikey) partIdsHtml += '<span class="part-id-digikey" data-digikey="' + escHtml(item.digikey) + '"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(item.digikey) + '</span>';
  if (item.pololu) partIdsHtml += '<span class="part-id-pololu" data-pololu="' + escHtml(item.pololu) + '"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(item.pololu) + '</span>';
  if (item.mouser) partIdsHtml += '<span class="part-id-mouser" data-mouser="' + escHtml(item.mouser) + '"><img class="vendor-icon" src="data/mouser-icon.svg">' + escHtml(item.mouser) + '</span>';
  if (!item.lcsc && !item.digikey && !item.pololu && !item.mouser) partIdsHtml += '<button class="no-dist-warn" title="No distributor PN \u2014 click to add">\u26A0 NO DIST. PN</button>';
  partIdsHtml += '</span>';

  // In label mode each inventory row carries a selection checkbox on BOTH the
  // left edge (in the Group cell) and the right edge (in .part-actions), so it
  // can be ticked from either side without scrolling the wide row across.
  var leftCheckboxHtml = isLabelMode() ? labelCheckboxHtml(invPartKey(item)) : '';

  // Green sparkle marking a recently-imported row, mirroring the scrollbar
  // gutter dot (same green glow + per-generation fade) so a dot in the gutter
  // can be visually traced to its row. Sits at the left edge of .part-actions.
  var newMarkHtml = options.importOpacity > 0
    ? '<span class="inv-row-new-mark" title="Recently imported" style="opacity:' + options.importOpacity + '">✦</span>'
    : '';

  var html =
    '<span class="inv-row-group-cell">' +
      leftCheckboxHtml +
      '<span class="inv-drag-handle" title="Drag to add to group">&#x2261;</span>' +
      sectionChipHtml +
    '</span>' +
    partIdsHtml +
    nearMissBadgeHtml +
    '<span class="part-mpn" title="' + escHtml(displayMpn) + '">' + escHtml(displayMpn) + '</span>' +
    '<span class="part-vendor">' + renderFanStack(item) + '</span>' +
    '<span class="part-unit-price">' + unitPriceStr + '</span>' +
    '<span class="part-value">' + valueStr + '</span>' +
    '<span class="part-qty" style="color:' + qtyColor + '">' + (showPriceWarn ? '<button class="price-warn-btn" title="No price data \u2014 click to set">\u26A0</button>' : '') + item.qty + '</span>' +
    (options.hideDescs
      ? '<span class="part-desc-pad" aria-hidden="true"></span>'
      : '<span class="part-desc"><span class="part-desc-inner" title="' + escHtml(displayDesc) + '">' + escHtml(displayDesc) + '</span></span>') +
    '<span class="part-actions">' +
      (isLabelMode()
        ? labelCheckboxHtml(invPartKey(item))
        : newMarkHtml + groupBtnStr + '<button class="btn-sm adj-btn" title="Adjust qty">Adjust</button>' + linkBtnStr) +
    '</span>';

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
  // Caller flips draggable on if a generic-parts flyout is currently open;
  // off by default so click-and-drag selects text.
  tr.draggable = false;
  tr.className = d.rowClass;
  if (d.isLinkingSource || d.isReverseLinkingSource) tr.classList.add("linking-source");
  if (d.isReverseTarget) tr.classList.add("link-target");

  var haveHtml = "" + d.invQty;
  if (d.altBadge) {
    var coveredCls = d.altBadge.covered ? " covered" : "";
    var expandedCls = d.altBadge.expanded ? " expanded" : "";
    haveHtml += '<br><span class="alt-badge' + coveredCls + expandedCls + '" data-part-key="' + escHtml(d.partKey) + '"><span class="chevron">\u25B8</span>+' + d.altBadge.altQty + ' (' + d.altBadge.badgeText + ')</span>';
  }
  if (d.memberBadge) {
    var mbExpandedCls = d.memberBadge.expanded ? " expanded" : "";
    haveHtml += '<br><span class="member-badge' + mbExpandedCls + '" data-part-key="' + escHtml(d.partKey) + '"><span class="chevron">\u25B8</span>' + d.memberBadge.memberCount + ' members</span>';
  }

  var adjBtnHtml = d.showAdjust ? '<button class="btn-sm adj-btn" title="Adjust qty">Adjust</button>' : '';
  var confirmBtnHtml = d.showConfirm
    ? '<button class="btn-sm confirm-btn" title="Confirm this match">Confirm</button>'
    : d.showUnconfirm
      ? '<button class="btn-sm unconfirm-btn" title="Revert to possible match">Unconfirm</button>'
      : '';
  var linkBtnHtml = d.showLink
    ? '<button class="btn-sm link-btn' + (d.linkActive ? ' active' : '') + '" title="' + (d.hasInv ? 'Link to missing BOM row' : 'Link to inventory part') + '">Link</button>'
    : '';
  var groupBtnHtml = d.showGroupFlyout && d.genericPartId
    ? '<button class="group-flyout-btn" title="View group" data-gp-id="' + escHtml(d.genericPartId) + '"><span class="generic-group-badge">\u229B</span></button>'
    : d.showGroupFlyout
    ? '<button class="group-flyout-btn group-flyout-create" title="Create group" data-bom-value="' + escHtml(d.bomValue) + '" data-bom-pkg="' + escHtml(d.bomFootprint) + '" data-bom-refs="' + escHtml(d.bomRefs) + '"><span class="generic-group-badge">+\u229B</span></button>'
    : '';

  // In label mode, swap the action-button group for a selection checkbox.
  // Missing BOM rows (no inventory item) have no printable part, so they show
  // an empty cell rather than a checkbox.
  var btnGroupContent = isLabelMode()
    ? (d.invKey ? labelCheckboxHtml(d.invKey) : '')
    : confirmBtnHtml + adjBtnHtml + linkBtnHtml + groupBtnHtml;

  tr.innerHTML =
    '<td class="refs-cell" title="' + escHtml(d.refs) + '"><div class="refs-scroll">' + colorizeRefs(d.refs) + '</div></td>' +
    '<td class="status">' + d.icon + '</td>' +
    '<td class="mono">' + (d.dispLcsc ? '<span' + (/^C\d{4,}$/i.test(d.dispLcsc) ? ' data-lcsc="' + escHtml(d.dispLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(d.dispLcsc) + '</span>' : '') + (d.dispLcsc && d.dispDigikey ? '<br>' : '') + (d.dispDigikey ? '<span data-digikey="' + escHtml(d.dispDigikey) + '" class="part-id-digikey"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(d.dispDigikey) + '</span>' : '') + ((d.dispLcsc || d.dispDigikey) && d.dispPololu ? '<br>' : '') + (d.dispPololu ? '<span data-pololu="' + escHtml(d.dispPololu) + '" class="part-id-pololu"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(d.dispPololu) + '</span>' : '') + ((d.dispLcsc || d.dispDigikey || d.dispPololu) && d.dispMouser ? '<br>' : '') + (d.dispMouser ? '<span data-mouser="' + escHtml(d.dispMouser) + '" class="part-id-mouser"><img class="vendor-icon" src="data/mouser-icon.svg">' + escHtml(d.dispMouser) + '</span>' : '') + '</td>' +
    '<td class="mono" title="' + escHtml(d.dispMpn) + '">' + escHtml(d.dispMpn) + '</td>' +
    '<td class="' + d.qtyClass + '" style="text-align:right;font-weight:600">' + d.effectiveQty + '</td>' +
    '<td class="inv-qty-cell ' + d.qtyClass + '" style="text-align:right;font-weight:600">' + haveHtml + '</td>' +
    '<td class="desc-cell' + (d.isMissing ? ' muted' : '') + '" title="' + escHtml(d.invDesc) + '">' + escHtml(d.invDesc) + (d.genericPartName ? '<span class="generic-via">via ' + escHtml(d.genericPartName) + '</span>' : '') + '</td>' +
    '<td class="mono" style="text-align:center">' + d.matchLabel +
      (d.footprintConfirmed && d.footprintCode ? ' <span class="match-signal-footprint" title="Footprint also matches: ' + escHtml(d.footprintCode) + '">+' + escHtml(d.footprintCode) + '</span>' : '') +
    '</td>' +
    '<td class="btn-group">' + btnGroupContent + '</td>';

  return tr;
}

// ── Alt rows builder ──

/**
 * Build alt inventory rows for a BOM part.
 * @param {Array<import('../types.js').InventoryItem>} alts - alternative inventory items
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
    if (altDigikey) altPartHtml += '<span data-digikey="' + escHtml(altDigikey) + '" class="part-id-digikey"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(altDigikey) + '</span>';
    if ((altLcsc || altDigikey) && altPololu) altPartHtml += '<br>';
    if (altPololu) altPartHtml += '<span data-pololu="' + escHtml(altPololu) + '" class="part-id-pololu"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(altPololu) + '</span>';
    if ((altLcsc || altDigikey || altPololu) && altMouser) altPartHtml += '<br>';
    if (altMouser) altPartHtml += '<span data-mouser="' + escHtml(altMouser) + '" class="part-id-mouser"><img class="vendor-icon" src="data/mouser-icon.svg">' + escHtml(altMouser) + '</span>';
    altTr.innerHTML =
      '<td></td>' +
      '<td></td>' +
      '<td class="mono">' + altPartHtml + '</td>' +
      '<td class="mono" title="' + escHtml(alt.mpn || '') + '">' + escHtml(alt.mpn || '') + '</td>' +
      '<td></td>' +
      '<td style="text-align:right;font-weight:600">' + alt.qty + '</td>' +
      '<td class="desc-cell" title="' + escHtml(alt.description) + ' ' + escHtml(alt.package) + '">' + escHtml(alt.description) + ' <span class="muted">' + escHtml(alt.package) + '</span></td>' +
      '<td></td>' +
      '<td class="btn-group"><button class="btn-sm swap-btn" title="Use this alt as the selected part">Swap</button><button class="btn-sm adj-btn" title="Adjust qty">Adjust</button></td>';
    rows.push(altTr);
  }
  return rows;
}

// ── Generic member rows builder ──

/**
 * Build member rows for a generic part group.
 * @param {Array<Object>} members - generic part members [{part_id, preferred, quantity}]
 * @param {string} partKey - parent BOM part key
 * @param {string} resolvedPartId - the currently resolved member part_id
 * @param {string} groupName - generic part name
 * @param {Array} inventory - full inventory for lookups
 * @returns {Array<HTMLTableRowElement>}
 */
export function renderMemberRows(members, partKey, resolvedPartId, groupName, inventory) {
  var rows = [];
  // Header row
  var headerTr = document.createElement("tr");
  headerTr.className = "member-header-row";
  headerTr.innerHTML = '<td colspan="9" class="member-header-cell">\u25C6 Generic group: ' + escHtml(groupName) + '</td>';
  rows.push(headerTr);

  // Build inventory lookup
  var invMap = {};
  for (var i = 0; i < inventory.length; i++) {
    var item = inventory[i];
    if (item.lcsc) invMap[item.lcsc.toUpperCase()] = item;
    if (item.mpn) invMap[item.mpn.toUpperCase()] = item;
  }

  // Sort: preferred first, then by quantity descending
  var sorted = members.slice().sort(function(a, b) {
    if (a.preferred !== b.preferred) return b.preferred - a.preferred;
    return b.quantity - a.quantity;
  });

  for (var j = 0; j < sorted.length; j++) {
    var m = sorted[j];
    var inv = invMap[m.part_id.toUpperCase()];
    var tr = document.createElement("tr");
    tr.className = "member-row";
    tr.dataset.memberFor = partKey;
    tr.dataset.memberPartId = m.part_id;
    var prefBadge = m.preferred ? '<span class="preferred-badge">\u2605</span> ' : '';
    var isCurrent = inv && invPartKey(inv) === resolvedPartId;
    var actionHtml = isCurrent
      ? '<span class="current-label">Current</span>'
      : '<button class="use-member-btn" title="Use this member">Use</button>';
    var mLcsc = inv ? (inv.lcsc || '') : m.part_id;
    var mMpn = inv ? (inv.mpn || '') : '';
    var mDesc = inv ? (inv.description || '') : '';
    var mQty = m.quantity;
    var qtyColor = mQty > 0 ? 'color:var(--color-green)' : 'color:var(--text-muted)';
    var partHtml = '';
    if (mLcsc) partHtml += '<span' + (/^C\d{4,}$/i.test(mLcsc) ? ' data-lcsc="' + escHtml(mLcsc) + '"' : '') + '>' + escHtml(mLcsc) + '</span>';
    tr.innerHTML =
      '<td></td>' +
      '<td>' + prefBadge + '</td>' +
      '<td class="mono">' + partHtml + '</td>' +
      '<td class="mono" title="' + escHtml(mMpn) + '">' + escHtml(mMpn) + '</td>' +
      '<td></td>' +
      '<td style="text-align:right;font-weight:600;' + qtyColor + '">' + mQty + '</td>' +
      '<td class="desc-cell" title="' + escHtml(mDesc) + '">' + escHtml(mDesc) + '</td>' +
      '<td></td>' +
      '<td class="btn-group">' + actionHtml + '<button class="adj-btn" title="Adjust qty">Adjust</button></td>';
    rows.push(tr);
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
  return '<button class="btn-md filter-btn' + (activeFilter === "all" ? " active" : "") + '" data-filter="all">All (' + c.total + ')</button>' +
    (c.manual > 0 ? '<button class="btn-md filter-btn' + (activeFilter === "manual" ? " active" : "") + '" data-filter="manual">Manual (' + c.manual + ')</button>' : '') +
    (c.confirmed > 0 ? '<button class="btn-md filter-btn' + (activeFilter === "confirmed" ? " active" : "") + '" data-filter="confirmed">Confirmed (' + c.confirmed + ')</button>' : '') +
    (c.generic > 0 ? '<button class="btn-md filter-btn' + (activeFilter === "generic" ? " active" : "") + '" data-filter="generic">Generic (' + c.generic + ')</button>' : '') +
    '<button class="btn-md filter-btn' + (activeFilter === "ok" ? " active" : "") + '" data-filter="ok">In Stock (' + c.ok + ')</button>' +
    '<button class="btn-md filter-btn' + (activeFilter === "short" ? " active" : "") + '" data-filter="short">Short (' + c.short + ')</button>' +
    '<button class="btn-md filter-btn' + (activeFilter === "possible" ? " active" : "") + '" data-filter="possible">Possible (' + c.possible + ')</button>' +
    '<button class="btn-md filter-btn' + (activeFilter === "missing" ? " active" : "") + '" data-filter="missing">Missing (' + c.missing + ')</button>' +
    (c.dnp > 0 ? '<button class="btn-md filter-btn' + (activeFilter === "dnp" ? " active" : "") + '" data-filter="dnp">DNP (' + c.dnp + ')</button>' : '');
}

// ── BOM comparison table header ──

/** Table id the BOM comparison table's column widths persist under. */
export var BOM_TABLE_ID = 'bom';

/**
 * The BOM comparison table's user-resizable columns, with the default width
 * (authored px) each <th> is emitted at. These numbers used to be inline
 * `style="width:NNpx"` literals in the thead string below; they now live here
 * once, so the header markup and the resize defaults cannot drift apart.
 *
 * Deliberately NOT resizable: Designators (its .refs-cell max-width lives in
 * css/tables.css, so widening the column alone would not widen the content),
 * the status icon, Description (the flexible column that absorbs the slack the
 * others give up), Match, and the sticky button column — that one's width is
 * the subject of the button-clipping tests.
 * @type {{id: string, def: number}[]}
 */
export var BOM_RESIZE_COLS = [
  { id: 'part', def: 110 },
  { id: 'mpn',  def: 140 },
  { id: 'need', def: 50 },
  { id: 'have', def: 50 },
];

/**
 * One resizable <th>: default width from BOM_RESIZE_COLS, plus a drag handle.
 * @param {string} id
 * @param {string} label
 * @returns {string}
 */
function bomTh(id, label) {
  var def = 0;
  for (var i = 0; i < BOM_RESIZE_COLS.length; i++) {
    if (BOM_RESIZE_COLS[i].id === id) def = BOM_RESIZE_COLS[i].def;
  }
  return '<th data-bom-col="' + id + '" style="width:' + def + 'px">' + label +
    colResizeHandleHtml(BOM_TABLE_ID, id) + '</th>';
}

/**
 * Returns the BOM comparison table header HTML.
 * @returns {string}
 */
export function renderBomTableHeader() {
  return '<thead><tr>' +
    '<th class="refs-col">Designators</th>' +
    '<th style="width:24px"></th>' +
    bomTh('part', 'Part #') +
    bomTh('mpn', 'MPN') +
    bomTh('need', 'Need') +
    bomTh('have', 'Have') +
    '<th>Description</th>' +
    '<th style="width:78px;text-align:center">Match</th>' +
    '<th class="btn-group-hdr"></th>' +
    '</tr></thead>';
}

export { countStatuses };

// ── Column header ────────────────────────────────────────

/** Table id the inventory grid's column widths persist under. */
export var INV_TABLE_ID = 'inv';

/**
 * The inventory grid's user-resizable columns. Each one is a CSS custom
 * property that BOTH the header cell (css/panels/inventory.css) and the
 * matching row cell read, which is why resizing writes the property rather
 * than a width: header and rows cannot fall out of alignment.
 *
 * `prop` is the width token; `minProp` the token holding that column's floor.
 * Values come from css/tokens.css — never hard-coded here.
 *
 * Deliberately NOT resizable: the Group dots (a 30px toggle), Description (the
 * flexible column that absorbs whatever the others take), and the ↺ button.
 * @type {{id: string, prop: string, minProp: string}[]}
 */
export var INV_RESIZE_COLS = [
  { id: 'partid', prop: '--inv-col-pn-w',       minProp: '--inv-col-pn-min-w' },
  { id: 'mpn',    prop: '--inv-col-mfgpn-w',    minProp: '--inv-col-mfgpn-min-w' },
  { id: 'vendor', prop: '--inv-col-vendor-w',   minProp: '--inv-col-vendor-min-w' },
  { id: 'unit',   prop: '--inv-col-unit-w',     minProp: '--inv-col-unit-min-w' },
  { id: 'value',  prop: '--inv-col-extprice-w', minProp: '--inv-col-extprice-min-w' },
  { id: 'qty',    prop: '--inv-col-stock-w',    minProp: '--inv-col-stock-min-w' },
];

/**
 * Build the inventory column-header HTML.
 * @param {object} viewState
 * @param {number} viewState.groupLevel        0 | 1 | 2
 * @param {string|null} viewState.sortColumn   null | "mpn" | "description" | "qty" | "unit_price" | "value"
 * @param {string|null} viewState.sortScope    null | "subsection" | "section" | "global"
 * @param {string|null} viewState.vendorGroupScope null | "subsection" | "section" | "global"
 * @param {boolean} viewState.hideDescs
 * @returns {string}
 */
export function renderInvColHeader(viewState) {
  function scopeDots(scope) {
    if (scope === 'subsection') return '·';                // · (U+00B7 MIDDLE DOT)
    if (scope === 'section')    return '··';           // ··
    if (scope === 'global')     return '···';     // ···
    return '';
  }
  function sortIndicator(col) {
    if (viewState.sortColumn !== col) return '';
    var isText = (col === 'mpn' || col === 'description');
    var arrow = isText ? '▲' : '▼';                    // ▲ (U+25B2) / ▼ (U+25BC)
    return '<span class="inv-col-sort-active">' + arrow + scopeDots(viewState.sortScope) + '</span>';
  }
  function vendorIndicator() {
    if (!viewState.vendorGroupScope) return '';
    return '<span class="inv-col-sort-active">⧉' + scopeDots(viewState.vendorGroupScope) + '</span>';  // ⧉ (U+29C9 TWO JOINED SQUARES)
  }
  function groupDots() {
    if (viewState.groupLevel === 0) return '●●';       // ●● (U+25CF BLACK CIRCLE)
    if (viewState.groupLevel === 1) return '●○';       // ●○ (U+25CB WHITE CIRCLE)
    return '○○';                                       // ○○
  }

  // When descriptions are hidden, emit an empty flex:1 spacer (separate
  // class so existing .inv-col-desc / .part-desc selectors still get a
  // count of zero) — the spacer absorbs the imbalance between the row's
  // wider .part-actions and the header's narrow ↺ button.
  var descCellHtml = viewState.hideDescs
    ? '<span class="inv-col-desc-pad" aria-hidden="true"></span>'
    // title: this column is the flexible one, so it is what gets squeezed in a
    // narrow panel; the CSS clips it rather than letting it overlap the reset
    // button, and the tooltip keeps the clipped label readable.
    : '<button class="inv-col-cell inv-col-desc" data-col="description" title="Description">Description ' + sortIndicator('description') + '</button>';

  // Resize handles are absolutely positioned inside their header cell (see
  // css/components/col-resize.css), so they add no flex child and no gap —
  // column alignment with the rows is unaffected.
  function grip(col) { return colResizeHandleHtml(INV_TABLE_ID, col); }

  return '<div class="inv-col-header">' +
    '<button class="inv-col-cell inv-col-group" data-col="group" title="Cycle grouping: full → sections → flat">' +
      '<span class="inv-col-group-dots">' + groupDots() + '</span>' +
    '</button>' +
    '<button class="inv-col-cell inv-col-partid" data-col="partid" title="Group by vendor">Part # ' + vendorIndicator() + grip('partid') + '</button>' +
    '<button class="inv-col-cell inv-col-mpn" data-col="mpn">MPN ' + sortIndicator('mpn') + grip('mpn') + '</button>' +
    '<span class="inv-col-vendor" title="Purchase source vendor(s)">Src' + grip('vendor') + '</span>' +
    '<button class="inv-col-cell inv-col-unit"  data-col="unit_price">Unit $ ' + sortIndicator('unit_price') + grip('unit') + '</button>' +
    '<button class="inv-col-cell inv-col-value" data-col="value">Total $ ' + sortIndicator('value') + grip('value') + '</button>' +
    '<button class="inv-col-cell inv-col-qty"   data-col="qty">Qty ' + sortIndicator('qty') + grip('qty') + '</button>' +
    descCellHtml +
    // Also resets column widths (js/col-resize.js binds to this same control):
    // one "put the view back" button, rather than a second glyph in a header
    // that is already width-constrained.
    '<button class="inv-col-cell inv-col-reset" data-col="reset" title="Reset sort, grouping and column widths">↺</button>' +  // ↺ (U+21BA ANTICLOCKWISE OPEN CIRCLE ARROW)
    '</div>';
}
