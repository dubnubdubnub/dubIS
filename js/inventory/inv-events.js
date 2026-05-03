/* inventory/inv-events.js — Event listener setup for the inventory panel.
   Extracted from init() to keep inventory-panel.js focused on rendering. */

import { EventBus, Events } from '../event-bus.js';
import { AppLog } from '../api.js';
import { store, saveInventoryView } from '../store.js';
import state from './inv-state.js';
import { nextScope } from './inv-sort-group.js';

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

  // ── Column header clicks ──
  function persistAndRender() {
    saveInventoryView({
      groupLevel: state.groupLevel,
      sortColumn: state.sortColumn,
      sortScope: state.sortScope,
      vendorGroupScope: state.vendorGroupScope,
    });
    render();
  }

  state.body.addEventListener("click", function (e) {
    var cell = e.target.closest(".inv-col-cell");
    if (!cell) return;
    var col = cell.dataset.col;

    if (col === "group") {
      state.groupLevel = (state.groupLevel + 1) % 3;
      // Drop scopes that are no longer reachable at the new level.
      if (state.groupLevel === 2) {
        if (state.sortScope && state.sortScope !== "global") { state.sortColumn = null; state.sortScope = null; }
        if (state.vendorGroupScope && state.vendorGroupScope !== "global") state.vendorGroupScope = null;
      } else if (state.groupLevel === 1) {
        if (state.sortScope === "subsection") { state.sortColumn = null; state.sortScope = null; }
        if (state.vendorGroupScope === "subsection") state.vendorGroupScope = null;
      }
      persistAndRender();
      return;
    }
    if (col === "reset") {
      state.groupLevel = 0;
      state.sortColumn = null;
      state.sortScope = null;
      state.vendorGroupScope = null;
      persistAndRender();
      return;
    }
    if (col === "partid") {
      state.vendorGroupScope = nextScope(state.groupLevel, state.vendorGroupScope);
      if (state.vendorGroupScope) { state.sortColumn = null; state.sortScope = null; }
      persistAndRender();
      return;
    }
    if (col === "mpn" || col === "unit_price" || col === "value" || col === "qty" || col === "description") {
      if (state.sortColumn !== col) {
        state.sortColumn = col;
        state.sortScope = nextScope(state.groupLevel, null);
      } else {
        state.sortScope = nextScope(state.groupLevel, state.sortScope);
        if (state.sortScope === null) state.sortColumn = null;
      }
      if (state.sortColumn) state.vendorGroupScope = null;
      persistAndRender();
      return;
    }
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
    setInventoryRowsDraggable(true);
  });

  EventBus.on(Events.FLYOUT_CLOSED, function () {
    import('../group-flyout/flyout-state.js').then(function (flyoutState) {
      if (flyoutState.flyouts.size === 0) {
        var panel = document.getElementById("panel-inventory");
        if (panel) panel.classList.remove("flyout-drag-active");
        setInventoryRowsDraggable(false);
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
}

function setInventoryRowsDraggable(on) {
  var rows = document.querySelectorAll(
    "#inventory-body .inv-part-row, #inventory-body tr[data-part-key]"
  );
  for (var i = 0; i < rows.length; i++) rows[i].draggable = on;
}

/**
 * True when at least one generic-parts flyout is open. Newly rendered rows
 * use this to decide whether to be draggable, so they pick up the right
 * state when the inventory re-renders while a flyout is active.
 */
export function isFlyoutDragActive() {
  var panel = document.getElementById("panel-inventory");
  return !!(panel && panel.classList.contains("flyout-drag-active"));
}
