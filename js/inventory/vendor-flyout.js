// @ts-check
/* vendor-flyout.js — Popover anchored to a favicon for editing/merging/refreshing a vendor. */

import { store } from '../store.js';
import { api, apiVendors } from '../api.js';
import { escHtml } from '../ui-helpers.js';

var PSEUDO_IDS = new Set(['v_self', 'v_salvage', 'v_unknown']);

/** @type {HTMLElement|null} */
var currentPopover = null;

/**
 * Close the currently open vendor popover, if any.
 */
export function closeVendorPopover() {
  if (currentPopover) {
    currentPopover.remove();
    currentPopover = null;
  }
}

/**
 * Open (or reopen) the vendor management popover anchored to anchorEl.
 * @param {HTMLElement} anchorEl - The favicon/icon element to anchor to
 * @param {string} vendorId - The vendor ID to manage
 */
export function openVendorPopover(anchorEl, vendorId) {
  // Close any existing popover first
  closeVendorPopover();

  var vendor = (store.vendors || []).find(function (v) { return v.id === vendorId; });
  if (!vendor) {
    vendor = { id: vendorId, name: vendorId, url: '', icon: '', favicon_path: '' };
  }

  var isPseudo = PSEUDO_IDS.has(vendorId);
  var popover = document.createElement('div');
  popover.className = 'vendor-popover';
  popover.dataset.vendorId = vendorId;

  // Build merge options (other real vendors)
  var mergeOptions = (store.vendors || [])
    .filter(function (v) { return v.id !== vendorId && !PSEUDO_IDS.has(v.id); })
    .map(function (v) {
      return '<option value="' + escHtml(v.id) + '">' + escHtml(v.name) + '</option>';
    }).join('');

  popover.innerHTML =
    '<div class="vendor-popover-header">' +
      '<span class="vendor-popover-title">' + escHtml(vendor.name || vendorId) + '</span>' +
      '<button class="vendor-popover-close" aria-label="Close">×</button>' +
    '</div>' +
    '<div class="vendor-popover-body">' +
      '<label class="vendor-popover-field">' +
        '<span class="vendor-popover-label">Name</span>' +
        '<input class="vendor-popover-input" data-field="name" type="text" value="' + escHtml(vendor.name || '') + '">' +
      '</label>' +
      (!isPseudo
        ? '<label class="vendor-popover-field">' +
            '<span class="vendor-popover-label">URL</span>' +
            '<input class="vendor-popover-input" data-field="url" type="text" value="' + escHtml(vendor.url || '') + '">' +
          '</label>'
        : '') +
      '<div class="vendor-popover-actions">' +
        '<button class="vendor-popover-btn vendor-popover-save">Save</button>' +
        (!isPseudo && mergeOptions
          ? '<select class="vendor-popover-merge-select"><option value="">Merge into…</option>' + mergeOptions + '</select>'
          : '') +
        (!isPseudo
          ? '<button class="vendor-popover-btn vendor-popover-refresh" title="Re-fetch favicon">Refresh favicon</button>'
          : '') +
      '</div>' +
    '</div>';

  document.body.appendChild(popover);
  currentPopover = popover;

  // Position anchored to the favicon
  var rect = anchorEl.getBoundingClientRect();
  popover.style.position = 'fixed';
  // Try to position below; clamp to viewport
  var top = rect.bottom + 4;
  var left = rect.left;
  popover.style.top = top + 'px';
  popover.style.left = left + 'px';
  // Clamp right edge after mount (popover width unknown until in DOM)
  requestAnimationFrame(function () {
    if (!currentPopover) return;
    var pw = currentPopover.offsetWidth;
    var maxLeft = window.innerWidth - pw - 8;
    if (left > maxLeft) currentPopover.style.left = Math.max(0, maxLeft) + 'px';
    var ph = currentPopover.offsetHeight;
    var maxTop = window.innerHeight - ph - 8;
    if (top > maxTop) currentPopover.style.top = Math.max(0, rect.top - ph - 4) + 'px';
  });

  // ── Event handlers ──

  // Close button
  popover.querySelector('.vendor-popover-close').addEventListener('click', function () {
    closeVendorPopover();
  });

  // Save
  popover.querySelector('.vendor-popover-save').addEventListener('click', function () {
    var nameInput = /** @type {HTMLInputElement} */ (popover.querySelector('[data-field="name"]'));
    var urlInput = /** @type {HTMLInputElement|null} */ (popover.querySelector('[data-field="url"]'));
    var name = nameInput ? nameInput.value.trim() : (vendor.name || '');
    var url = urlInput ? urlInput.value.trim() : (vendor.url || '');
    apiVendors.upsert(vendorId, name, url).then(function () {
      return api('rebuild_inventory');
    }).then(function (freshInventory) {
      if (freshInventory) {
        store.onInventoryUpdated(freshInventory);
      }
      closeVendorPopover();
    }).catch(function (err) {
      console.error('[vendor-flyout] save failed:', err);
    });
  });

  // Merge select
  var mergeSelect = /** @type {HTMLSelectElement|null} */ (popover.querySelector('.vendor-popover-merge-select'));
  if (mergeSelect) {
    mergeSelect.addEventListener('change', function () {
      var dstId = mergeSelect.value;
      if (!dstId) return;
      if (!window.confirm('Merge "' + (vendor.name || vendorId) + '" into "' + dstId + '"? This cannot be undone.')) {
        mergeSelect.value = '';
        return;
      }
      apiVendors.merge(vendorId, dstId).then(function () {
        return api('rebuild_inventory');
      }).then(function (freshInventory) {
        if (freshInventory) {
          store.onInventoryUpdated(freshInventory);
        }
        closeVendorPopover();
      }).catch(function (err) {
        console.error('[vendor-flyout] merge failed:', err);
        mergeSelect.value = '';
      });
    });
  }

  // Refresh favicon
  var refreshBtn = popover.querySelector('.vendor-popover-refresh');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function () {
      var urlInput = /** @type {HTMLInputElement|null} */ (popover.querySelector('[data-field="url"]'));
      var url = urlInput ? urlInput.value.trim() : (vendor.url || '');
      if (!url) return;
      refreshBtn.textContent = 'Fetching…';
      refreshBtn.setAttribute('disabled', 'true');
      apiVendors.fetchFavicon(url).then(function () {
        closeVendorPopover();
      }).catch(function (err) {
        console.error('[vendor-flyout] fetchFavicon failed:', err);
        refreshBtn.textContent = 'Refresh favicon';
        refreshBtn.removeAttribute('disabled');
      });
    });
  }

  // Close on outside click
  setTimeout(function () {
    document.addEventListener('click', function outsideClick(e) {
      if (currentPopover && !currentPopover.contains(/** @type {Node} */ (e.target))) {
        closeVendorPopover();
        document.removeEventListener('click', outsideClick);
      }
    });
  }, 0);
}
