/* inventory/inv-events.js — Event listener setup for the inventory panel.
   Extracted from init() to keep inventory-panel.js focused on rendering. */

import { EventBus, Events } from '../event-bus.js';
import { AppLog } from '../api.js';
import { App } from '../store.js';
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
    state.clearFilterBtn.classList.toggle("hidden", hideFilters);
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
    App.links.clearAll();
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
    if (e.key === "Escape" && App.links.linkingMode) {
      if (App.links.linkingBomRow) App.links.setReverseLinkingMode(false);
      else App.links.setLinkingMode(false);
    }
  });
}
