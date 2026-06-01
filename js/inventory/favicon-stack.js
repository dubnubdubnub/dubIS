// @ts-check
/* favicon-stack.js — Fan-stack of vendor favicons for inventory rows +
   hover flyout showing purchase-order history. */

import { store } from '../store.js';
import { escHtml, vendorIconSrc } from '../ui-helpers.js';

/** Number of most-recent POs shown in the inline grid stack. */
var MAX_VISIBLE = 3;
/** Per-icon cascade offset (px), applied both right and down. */
var STACK_OFFSET_PX = 6;
/** Icon edge length (px); mirrors `.fan-icon` width/height in vendor.css. */
var ICON_SIZE_PX = 16;

/**
 * Render a cascading stack of vendor favicons for a part's most recent POs.
 *
 * Shows up to MAX_VISIBLE icons — one per PO (no vendor dedup), the most recent
 * PO on top. po_history is chronological oldest→newest, so the last entries are
 * the most recent. Icons cascade down-right with the most recent at (0,0) and
 * the highest z-index, so each older icon's bottom+right edges peek out behind
 * the one in front. The full per-PO history lives in the hover flyout
 * (buildHoverFlyout); this is the at-a-glance view.
 * @param {Object} part - inventory item with po_history, primary_vendor_id
 * @returns {string} HTML string
 */
export function renderFanStack(part) {
  var poHistory = part.po_history || [];
  if (poHistory.length === 0 && !part.primary_vendor_id) {
    return '';
  }

  // Build po_id → vendor_id lookup from store
  var pos = store.purchaseOrders || [];
  var poVendorMap = {};
  for (var i = 0; i < pos.length; i++) {
    poVendorMap[pos[i].po_id] = pos[i].vendor_id;
  }

  // Take the MAX_VISIBLE most recent POs (end of the chronological list) and
  // reverse so the newest is first (index 0 = front of the stack). One vendor
  // id per PO — the same vendor may legitimately appear more than once.
  var recent = poHistory.slice(-MAX_VISIBLE).reverse();
  var vendorIds = [];
  for (var j = 0; j < recent.length; j++) {
    var vid = poVendorMap[recent[j]];
    if (vid) vendorIds.push(vid);
  }

  // Fall back to primary_vendor_id if no PO history resolved
  if (vendorIds.length === 0 && part.primary_vendor_id) {
    vendorIds.push(part.primary_vendor_id);
  }

  if (vendorIds.length === 0) return '';

  // Build vendor lookup
  var vendorMap = {};
  (store.vendors || []).forEach(function (v) { vendorMap[v.id] = v; });

  // Render the cascade. Index 0 (most recent) sits at (0,0) with the highest
  // z-index; each older icon is offset down-right and layered behind.
  var n = vendorIds.length;
  var icons = '';
  for (var k = 0; k < n; k++) {
    var v = vendorMap[vendorIds[k]];
    if (!v) continue;
    var off = k * STACK_OFFSET_PX;
    var style = 'left:' + off + 'px;top:' + off + 'px;z-index:' + (n - k);
    var iconHtml = v.icon
      ? '<span class="fan-icon fan-icon-emoji" style="' + style + '">' + escHtml(v.icon) + '</span>'
      : (v.favicon_path
        ? '<img class="fan-icon fan-icon-img" src="' + escHtml(vendorIconSrc(v.favicon_path)) + '" alt="" style="' + style + '">'
        : '<span class="fan-icon fan-icon-empty" style="' + style + '"></span>');
    icons += iconHtml;
  }

  var span = (n - 1) * STACK_OFFSET_PX + ICON_SIZE_PX;

  return '<span class="favicon-fan-stack" data-part-key="' + escHtml((part.lcsc || part.mpn || '')) + '" style="width:' + span + 'px;height:' + span + 'px">' +
    icons +
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
        iconHtml = '<img class="flyout-favicon flyout-favicon-img" src="' + escHtml(vendorIconSrc(vendor.favicon_path)) + '" alt="">';
      }
    }
    rows += '<div class="flyout-po-row" data-po-id="' + escHtml(poId) + '">' +
      iconHtml +
      '<span class="flyout-vendor-name">' + escHtml(vendorName) + '</span>' +
      (date ? '<span class="flyout-po-date">' + escHtml(date) + '</span>' : '') +
      '<span class="flyout-po-id">' + escHtml(poId) + '</span>' +
      '</div>';
  }

  div.innerHTML = '<div class="flyout-po-header">Purchase history (' + poHistory.length + ')</div>' + rows;
  return div;
}
