// @ts-check
/* favicon-stack.js — Fan-stack of vendor favicons for inventory rows +
   hover flyout showing purchase-order history. */

import { store } from '../store.js';
import { escHtml } from '../ui-helpers.js';

/**
 * Render a fanned-stack of vendor favicons for a part.
 * Uses po_history (list of po_ids) to find which vendors supplied this part.
 * @param {Object} part - inventory item with po_history, primary_vendor_id
 * @returns {string} HTML string
 */
export function renderFanStack(part) {
  var poHistory = part.po_history || [];
  if (poHistory.length === 0 && !part.primary_vendor_id) {
    return '';
  }

  // Collect unique vendor ids, preserving chronological order (latest first)
  var seen = new Set();
  var vendorIds = [];

  // Build po_id → vendor_id lookup from store
  var pos = store.purchaseOrders || [];
  var poVendorMap = {};
  for (var i = 0; i < pos.length; i++) {
    poVendorMap[pos[i].po_id] = pos[i].vendor_id;
  }

  // Walk po_history (which is chronological / append order), collect unique vendor ids
  for (var j = 0; j < poHistory.length; j++) {
    var vid = poVendorMap[poHistory[j]];
    if (vid && !seen.has(vid)) {
      seen.add(vid);
      vendorIds.push(vid);
    }
  }

  // Fall back to primary_vendor_id if no PO history resolved
  if (vendorIds.length === 0 && part.primary_vendor_id) {
    vendorIds.push(part.primary_vendor_id);
  }

  if (vendorIds.length === 0) return '';

  // Build vendor lookup
  var vendorMap = {};
  (store.vendors || []).forEach(function (v) { vendorMap[v.id] = v; });

  // Render stacked icons (max 3 visible, rest hidden under the stack)
  var maxVisible = 3;
  var icons = '';
  for (var k = 0; k < vendorIds.length && k < maxVisible; k++) {
    var v = vendorMap[vendorIds[k]];
    if (!v) continue;
    var offset = k * 6; // fan offset in px
    var iconHtml = v.icon
      ? '<span class="fan-icon fan-icon-emoji" style="left:' + offset + 'px">' + escHtml(v.icon) + '</span>'
      : (v.favicon_path
        ? '<img class="fan-icon fan-icon-img" src="' + escHtml(v.favicon_path) + '" alt="" style="left:' + offset + 'px">'
        : '<span class="fan-icon fan-icon-empty" style="left:' + offset + 'px"></span>');
    icons += iconHtml;
  }

  var extraCount = vendorIds.length > maxVisible ? vendorIds.length - maxVisible : 0;
  var extraHtml = extraCount > 0
    ? '<span class="fan-icon fan-icon-extra" style="left:' + (maxVisible * 6) + 'px">+' + extraCount + '</span>'
    : '';

  var totalWidth = Math.min(vendorIds.length, maxVisible) * 6 + 16 + (extraCount > 0 ? 14 : 0);

  return '<span class="favicon-fan-stack" data-part-key="' + escHtml((part.lcsc || part.mpn || '')) + '" style="width:' + totalWidth + 'px">' +
    icons + extraHtml +
    '</span>';
}

/**
 * Build the hover flyout DOM element for a favicon fan stack.
 * @param {Object} part - inventory item with po_history
 * @returns {HTMLElement}
 */
export function buildHoverFlyout(part) {
  var div = document.createElement('div');
  div.className = 'favicon-fan-flyout';

  var poHistory = part.po_history || [];
  if (poHistory.length === 0) {
    div.textContent = 'No purchase history';
    return div;
  }

  // Build lookups
  var pos = store.purchaseOrders || [];
  var poMap = {};
  for (var i = 0; i < pos.length; i++) { poMap[pos[i].po_id] = pos[i]; }

  var vendorMap = {};
  (store.vendors || []).forEach(function (v) { vendorMap[v.id] = v; });

  // Render one row per PO in order
  var rows = '';
  for (var j = 0; j < poHistory.length; j++) {
    var poId = poHistory[j];
    var po = poMap[poId];
    if (!po) continue;
    var vendor = vendorMap[po.vendor_id];
    var vendorName = vendor ? vendor.name : po.vendor_id;
    var date = po.purchase_date || '';
    var iconHtml = '';
    if (vendor) {
      if (vendor.icon) {
        iconHtml = '<span class="flyout-favicon flyout-favicon-emoji">' + escHtml(vendor.icon) + '</span>';
      } else if (vendor.favicon_path) {
        iconHtml = '<img class="flyout-favicon flyout-favicon-img" src="' + escHtml(vendor.favicon_path) + '" alt="">';
      }
    }
    rows += '<div class="flyout-po-row">' +
      iconHtml +
      '<span class="flyout-vendor-name">' + escHtml(vendorName) + '</span>' +
      (date ? '<span class="flyout-po-date">' + escHtml(date) + '</span>' : '') +
      '<span class="flyout-po-id">' + escHtml(poId) + '</span>' +
      '</div>';
  }

  div.innerHTML = '<div class="flyout-po-header">Purchase history (' + poHistory.length + ')</div>' + rows;
  return div;
}
