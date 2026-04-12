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
    '<span class="part-actions">' + groupBtnStr + '<button class="btn-sm adj-btn" title="Adjust qty">Adjust</button>' +
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

  tr.innerHTML =
    '<td class="refs-cell" title="' + escHtml(d.refs) + '">' + colorizeRefs(d.refs) + '</td>' +
    '<td class="status">' + d.icon + '</td>' +
    '<td class="mono">' + (d.dispLcsc ? '<span' + (/^C\d{4,}$/i.test(d.dispLcsc) ? ' data-lcsc="' + escHtml(d.dispLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(d.dispLcsc) + '</span>' : '') + (d.dispLcsc && d.dispDigikey ? '<br>' : '') + (d.dispDigikey ? '<span data-digikey="' + escHtml(d.dispDigikey) + '" class="part-id-digikey"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(d.dispDigikey) + '</span>' : '') + ((d.dispLcsc || d.dispDigikey) && d.dispPololu ? '<br>' : '') + (d.dispPololu ? '<span data-pololu="' + escHtml(d.dispPololu) + '" class="part-id-pololu"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(d.dispPololu) + '</span>' : '') + ((d.dispLcsc || d.dispDigikey || d.dispPololu) && d.dispMouser ? '<br>' : '') + (d.dispMouser ? '<span data-mouser="' + escHtml(d.dispMouser) + '" class="part-id-mouser"><img class="vendor-icon" src="data/mouser-icon.svg">' + escHtml(d.dispMouser) + '</span>' : '') + '</td>' +
    '<td class="mono" title="' + escHtml(d.dispMpn) + '">' + escHtml(d.dispMpn) + '</td>' +
    '<td class="' + d.qtyClass + '" style="text-align:right;font-weight:600">' + d.effectiveQty + '</td>' +
    '<td class="inv-qty-cell ' + d.qtyClass + '" style="text-align:right;font-weight:600">' + haveHtml + '</td>' +
    '<td class="desc-cell' + (d.isMissing ? ' muted' : '') + '" title="' + escHtml(d.invDesc) + '">' + escHtml(d.invDesc) + (d.genericPartName ? '<span class="generic-via">via ' + escHtml(d.genericPartName) + '</span>' : '') + '</td>' +
    '<td class="mono" style="text-align:center">' + d.matchLabel + '</td>' +
    '<td class="btn-group">' + confirmBtnHtml + adjBtnHtml + linkBtnHtml + groupBtnHtml + '</td>';

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
