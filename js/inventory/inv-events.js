/* inventory/inv-events.js — Event listener setup for the inventory panel.
   Extracted from init() to keep inventory-panel.js focused on rendering. */

import { EventBus, Events } from '../event-bus.js';
import { AppLog } from '../api.js';
import { store } from '../store.js';
import { escHtml } from '../ui-helpers.js';
import { inferDistributor } from './inventory-logic.js';
import state from './inv-state.js';
import { buildHoverFlyout } from './favicon-stack.js';
import { openVendorPopover } from './vendor-flyout.js';

/**
 * Wire up all DOM event listeners and EventBus subscriptions.
 * @param {object} handlers - core logic functions from inventory-panel.js
 */
export function setupEvents(handlers) {
  var render = handlers.render;
  var updateDistFilterUI = handlers.updateDistFilterUI;

  // ── ResizeObserver for description hiding + filter bar visibility ──
  var FILTER_BAR_MIN_WIDTH = 700;
  new ResizeObserver(function (entries) {
    var w = entries[0].contentRect.width;
    var narrow = w < state.DESC_HIDE_WIDTH;
    if (narrow !== state.hideDescs) { state.hideDescs = narrow; render(); }
    var compact = w < FILTER_BAR_MIN_WIDTH;
    state.distFilterBar.classList.toggle("compact", compact);
    state.clearFilterBtn.classList.toggle("compact", compact);
  }).observe(state.body);

  // Log app dimensions on resize
  window.addEventListener("resize", function () {
    AppLog.info("Window: " + window.innerWidth + "\u00D7" + window.innerHeight + "  inv-body: " + state.body.offsetWidth + "\u00D7" + state.body.offsetHeight);
  });

  // ── Search ──
  var searchTimer;
  state.searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      updateDistFilterUI();
      render();
      // Sync to active flyout
      import('../group-flyout/flyout-state.js').then(function (flyoutState) {
        if (!flyoutState.activeFlyoutId) return;
        var inst = flyoutState.flyouts.get(flyoutState.activeFlyoutId);
        if (inst) {
          inst.searchText = state.searchInput.value;
          import('../group-flyout/flyout-panel.js').then(function (panel) {
            panel.rerenderFlyout(flyoutState.activeFlyoutId);
          });
        }
      });
    }, 150);
  });

  // ── Distributor filter buttons (multi-select) ──
  state.distFilterBar.addEventListener("click", function (e) {
    var btn = e.target.closest(".dist-filter-btn");
    if (!btn) return;
    var dist = btn.dataset.distributor;
    if (state.activeDistributors.has(dist)) state.activeDistributors.delete(dist);
    else state.activeDistributors.add(dist);
    updateDistFilterUI();
    render();
  });

  state.clearFilterBtn.addEventListener("click", function () {
    if (state.activeDistributors.size === 0 && !state.searchInput.value) return;
    state.activeDistributors.clear();
    state.searchInput.value = "";
    updateDistFilterUI();
    render();
  });

  // Caret on Direct pill toggles vendor sub-pill panel
  var directBtn = document.querySelector('[data-distributor="direct"]');
  if (directBtn) {
    var caret = directBtn.querySelector('.dist-vendor-caret');
    if (caret) {
      caret.addEventListener('click', function (e) {
        e.stopPropagation();
        toggleVendorSubpills();
      });
    }
  }

  // ── EventBus subscriptions ──
  EventBus.on(Events.INVENTORY_LOADED, function () { render(); });
  EventBus.on(Events.INVENTORY_UPDATED, function () { render(); });
  EventBus.on(Events.PREFS_CHANGED, function () { render(); });

  EventBus.on(Events.BOM_LOADED, function (data) {
    state.bomData = data;
    render();
  });

  EventBus.on(Events.BOM_CLEARED, function () {
    state.bomData = null;
    state.activeFilter = "all";
    state.activeDistributors.clear();
    updateDistFilterUI();
    state.expandedAlts = new Set();
    state.expandedMembers = new Set();
    store.links.clearAll();
    render();
  });

  EventBus.on(Events.LINKING_MODE, function () { render(); });

  EventBus.on(Events.FLYOUT_SEARCH_CHANGED, function (data) {
    if (state.searchInput && data && typeof data.searchText === "string") {
      state.searchInput.value = data.searchText;
      render();
    }
  });

  EventBus.on(Events.FLYOUT_ACTIVE_CHANGED, function (data) {
    if (!data || !data.gpId) return;
    import('../group-flyout/flyout-state.js').then(function (flyoutState) {
      var inst = flyoutState.flyouts.get(data.gpId);
      if (inst && state.searchInput) {
        state.searchInput.value = inst.searchText;
        render();
      }
    });
  });

  EventBus.on(Events.FLYOUT_OPENED, function () {
    var panel = document.getElementById("panel-inventory");
    if (panel) panel.classList.add("flyout-drag-active");
  });

  EventBus.on(Events.FLYOUT_CLOSED, function () {
    import('../group-flyout/flyout-state.js').then(function (flyoutState) {
      if (flyoutState.flyouts.size === 0) {
        var panel = document.getElementById("panel-inventory");
        if (panel) panel.classList.remove("flyout-drag-active");
      }
    });
  });

  // ── Escape key for linking mode ──
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && store.links.linkingMode) {
      if (store.links.linkingBomRow) store.links.setReverseLinkingMode(false);
      else store.links.setLinkingMode(false);
    }
  });

  // ── Vendor sub-pill filter ──
  window.addEventListener('inv-filter-changed', function () {
    render();
  });

  // ── Favicon fan-stack hover flyout ──
  var activeFanFlyout = null;
  var fanFlyoutTimer = null;

  state.body.addEventListener('mouseover', function (e) {
    var stack = e.target.closest('.favicon-fan-stack');
    if (!stack) return;
    clearTimeout(fanFlyoutTimer);
    fanFlyoutTimer = setTimeout(function () {
      // Remove existing flyout
      if (activeFanFlyout) { activeFanFlyout.remove(); activeFanFlyout = null; }
      var partKey = stack.dataset.partKey || '';
      var inv = store.inventory || [];
      var part = null;
      for (var i = 0; i < inv.length; i++) {
        var p = inv[i];
        var key = (p.lcsc || p.mpn || '');
        if (key === partKey) { part = p; break; }
      }
      if (!part) return;
      var flyout = buildHoverFlyout(part);
      flyout.style.position = 'fixed';
      var rect = stack.getBoundingClientRect();
      flyout.style.top = (rect.bottom + 4) + 'px';
      flyout.style.left = rect.left + 'px';
      document.body.appendChild(flyout);
      activeFanFlyout = flyout;
    }, 120);
  });

  state.body.addEventListener('mouseout', function (e) {
    var stack = e.target.closest('.favicon-fan-stack');
    if (!stack) return;
    clearTimeout(fanFlyoutTimer);
    if (activeFanFlyout) { activeFanFlyout.remove(); activeFanFlyout = null; }
  });

  // ── Vendor management popover (click on favicon icon) ──
  document.addEventListener('click', function (e) {
    var fav = /** @type {Element} */ (e.target).closest('.fan-icon, .sub-favicon');
    if (!fav) return;
    e.stopPropagation();
    var vid = '';
    var subpill = fav.closest('.vendor-subpill');
    if (subpill) {
      vid = /** @type {HTMLElement} */ (subpill).dataset.vendorId || '';
    } else {
      var stack = fav.closest('.favicon-fan-stack');
      if (stack) {
        var partKey = /** @type {HTMLElement} */ (stack).dataset.partKey || '';
        var inv = store.inventory || [];
        var part = null;
        for (var i = 0; i < inv.length; i++) {
          if ((inv[i].lcsc || inv[i].mpn || '') === partKey) { part = inv[i]; break; }
        }
        vid = part ? (part.primary_vendor_id || 'v_unknown') : '';
      }
    }
    if (vid) openVendorPopover(/** @type {HTMLElement} */ (fav), vid);
  });
}

/**
 * Render vendor sub-pill panel contents.
 */
function renderVendorSubpills() {
  var panel = document.getElementById('vendor-subpills-panel');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'vendor-subpills-panel';
    panel.className = 'vendor-subpills-panel hidden';
    var filterBar = document.querySelector('.dist-filter-bar');
    if (filterBar) filterBar.after(panel);
  }
  var counts = {};
  (store.inventory || []).forEach(function (p) {
    if (inferDistributor(p) === 'direct') {
      var vid = p.primary_vendor_id || 'v_unknown';
      counts[vid] = (counts[vid] || 0) + 1;
    }
  });
  var vendors = (store.vendors || []).filter(function (v) {
    return counts[v.id] || ['v_self', 'v_salvage', 'v_unknown'].includes(v.id);
  });
  panel.innerHTML = vendors.map(function (v) {
    var selected = state.selectedVendorIds.has(v.id) ? 'selected' : '';
    var iconHtml = v.icon
      ? '<span class="sub-favicon">' + escHtml(v.icon) + '</span>'
      : (v.favicon_path
        ? '<img class="sub-favicon" src="' + escHtml(v.favicon_path) + '" alt="">'
        : '<span class="sub-favicon-empty"></span>');
    return '<button class="vendor-subpill ' + selected + '" data-vendor-id="' + escHtml(v.id) + '">' +
      iconHtml + '<span class="sub-name">' + escHtml(v.name) + '</span>' +
      '<span class="sub-count">' + escHtml(String(counts[v.id] || 0)) + '</span>' +
      '</button>';
  }).join('');
  panel.querySelectorAll('.vendor-subpill').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var vid = btn.dataset.vendorId;
      if (state.selectedVendorIds.has(vid)) state.selectedVendorIds.delete(vid);
      else state.selectedVendorIds.add(vid);
      btn.classList.toggle('selected');
      window.dispatchEvent(new CustomEvent('inv-filter-changed'));
    });
  });
}

/**
 * Toggle vendor sub-pill panel visibility.
 */
function toggleVendorSubpills() {
  var panel = document.getElementById('vendor-subpills-panel');
  if (!panel) {
    renderVendorSubpills();
    var p = document.getElementById('vendor-subpills-panel');
    if (p) p.classList.remove('hidden');
  } else {
    panel.classList.toggle('hidden');
    renderVendorSubpills();
  }
}
