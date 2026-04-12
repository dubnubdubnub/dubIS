/* inventory/inv-events.js — Event listener setup for the inventory panel.
   Extracted from init() to keep inventory-panel.js focused on rendering. */

import { EventBus, Events } from '../event-bus.js';
import { AppLog } from '../api.js';
import { store } from '../store.js';
import state from './inv-state.js';

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
    var hideFilters = w < FILTER_BAR_MIN_WIDTH;
    state.distFilterBar.classList.toggle("hidden", hideFilters);
    state.clearFilterBtn.classList.toggle("hidden", hideFilters && !state.activeDistributor);
  }).observe(state.body);

  // Log app dimensions on resize
  window.addEventListener("resize", function () {
    AppLog.info("Window: " + window.innerWidth + "\u00D7" + window.innerHeight + "  inv-body: " + state.body.offsetWidth + "\u00D7" + state.body.offsetHeight);
  });

  // ── Search ──
  var searchTimer;
  state.searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { render(); }, 150);
  });

  // ── Distributor filter buttons ──
  state.distFilterBar.addEventListener("click", function (e) {
    var btn = e.target.closest(".dist-filter-btn");
    if (!btn) return;
    var dist = btn.dataset.distributor;
    state.activeDistributor = (state.activeDistributor === dist) ? null : dist;
    updateDistFilterUI();
    render();
  });

  state.clearFilterBtn.addEventListener("click", function () {
    if (state.activeDistributor === null) return;
    state.activeDistributor = null;
    updateDistFilterUI();
    render();
  });

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
    state.activeDistributor = null;
    updateDistFilterUI();
    state.expandedAlts = new Set();
    state.expandedMembers = new Set();
    store.links.clearAll();
    render();
  });

  EventBus.on(Events.LINKING_MODE, function () { render(); });

  // ── Escape key for linking mode ──
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && store.links.linkingMode) {
      if (store.links.linkingBomRow) store.links.setReverseLinkingMode(false);
      else store.links.setLinkingMode(false);
    }
  });
}
