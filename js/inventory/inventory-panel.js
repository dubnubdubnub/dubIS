// @ts-check
/* inventory-panel.js — Thin wiring for the inventory panel.
   init(), top-level render(), distributor-filter UI, EventBus wiring.
   Delegates to inv-tree-render.js, inv-bom-mode.js, inv-row-build.js, inv-mutations.js. */

import { store } from '../store.js';
import { countByDistributor } from './inventory-logic.js';
import {
  renderInvColHeader, INV_TABLE_ID, INV_RESIZE_COLS, BOM_TABLE_ID, BOM_RESIZE_COLS,
} from './inv-html-builders.js';
import { getLayoutTokenPx } from '../layout-tokens.js';
import { registerColResizeTable, applyColWidths } from '../col-resize.js';
import state from './inv-state.js';
import { setupEvents } from './inv-events.js';
import { setupRowDelegation } from './inv-row-build.js';
import { renderNormalInventory } from './inv-tree-render.js';
import { renderBomComparison, renderRemainingInventory } from './inv-bom-mode.js';
import { refreshImportMarkers } from './inv-import-markers.js';
import { initSavedViewsUI } from './saved-views-ui.js';
import { initFilterChipsBar } from './filter-chips-bar.js';

// ── Init ──

export function init() {
  state.body = document.getElementById("inventory-body");
  state.searchInput = document.getElementById("inv-search");
  state.clearFilterBtn = document.getElementById("clear-dist-filter");
  state.distFilterBar = document.getElementById("dist-filter-bar");

  // Store render callback in state so extracted modules can trigger re-renders
  state._render = render;

  setupEvents({ render: render, updateDistFilterUI: updateDistFilterUI });

  // Delegated per-row handlers (adjust/price/link buttons, badges, checkboxes,
  // row clicks) — one listener set on the body instead of closures per row.
  setupRowDelegation(state.body);

  // ── Saved Views toolbar button ──
  initSavedViewsUI(state, render, updateDistFilterUI);

  // ── Filter chips bar ──
  initFilterChipsBar(state, render);

  if (window.ResizeObserver && state.body) {
    new ResizeObserver(() => refreshImportMarkers()).observe(state.body);
  }

  initColumnResizing();
}

// ── Column resizing ──
//
// Both tables that render inside #inventory-body opt in through the same
// generic module (js/col-resize.js); all each one declares is how a width is
// applied. Nothing else about resizing lives in this panel.

function initColumnResizing() {
  var body = state.body;
  if (!body) return;

  // The inventory grid: widths are CSS custom properties that the header cells
  // AND the row cells both read, so writing the property on the grid container
  // moves both at once. Defaults and floors come from css/tokens.css.
  registerColResizeTable({
    id: INV_TABLE_ID,
    cols: INV_RESIZE_COLS.map(function (c) {
      return {
        id: c.id,
        def: getLayoutTokenPx(c.prop),
        min: getLayoutTokenPx(c.minProp),
        max: getLayoutTokenPx('--col-resize-max-w'),
      };
    }),
    container: function () { return body; },
    apply: function (colId, px) {
      for (var i = 0; i < INV_RESIZE_COLS.length; i++) {
        if (INV_RESIZE_COLS[i].id === colId) {
          body.style.setProperty(INV_RESIZE_COLS[i].prop, px + 'px');
        }
      }
    },
    // The header's existing ↺ already means "put this view back to defaults";
    // widths are part of that view, so it resets them too.
    resetSelector: '.inv-col-cell[data-col="reset"]',
  });

  // The BOM comparison table is a real <table> with table-layout: fixed, so a
  // width belongs on the <th>.
  registerColResizeTable({
    id: BOM_TABLE_ID,
    cols: BOM_RESIZE_COLS.map(function (c) {
      return {
        id: c.id,
        def: c.def,
        min: getLayoutTokenPx('--col-resize-min-w'),
        max: getLayoutTokenPx('--col-resize-max-w'),
      };
    }),
    container: function () { return body; },
    apply: function (colId, px) {
      var ths = body.querySelectorAll('thead th[data-bom-col="' + colId + '"]');
      for (var i = 0; i < ths.length; i++) {
        /** @type {HTMLElement} */ (ths[i]).style.width = px + 'px';
      }
    },
  });

  // Stored widths are applied from app-init.js once loadPreferences() has
  // resolved (panels mount first), alongside applyStoredZoom/applyStoredCollapse.
  applyColWidths();
}

// ── Distributor filter UI state ──

function updateDistFilterUI() {
  var btns = state.distFilterBar.querySelectorAll(".dist-filter-btn");
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.toggle("active", state.activeDistributors.has(btns[i].dataset.distributor));
  }
  var hasChips = !!(state.activePredicate && state.activePredicate.rules && state.activePredicate.rules.length > 0);
  state.clearFilterBtn.disabled = (state.activeDistributors.size === 0 && !state.searchInput.value && !hasChips);
}

function updateDistCounts() {
  var counts = countByDistributor(store.inventory);
  var btns = state.distFilterBar.querySelectorAll(".dist-filter-btn");
  for (var i = 0; i < btns.length; i++) {
    var dist = btns[i].dataset.distributor;
    var label = btns[i].querySelector(".dist-label");
    if (label) label.textContent = dist.charAt(0).toUpperCase() + dist.slice(1) + " (" + counts[dist] + ")";
  }
}

// ── Main render ──

function render() {
  // Preserve the scroll position across the full rebuild. Wiping innerHTML drops
  // it, and the BOM-comparison path in particular does not restore it — so any
  // inventory mutation (price save, qty adjust) would otherwise jump the list
  // back toward the top. Captured here and restored at the end.
  var prevScroll = state.body.scrollTop;
  state.body.innerHTML = "";
  updateDistCounts();
  // Sticky offset for parent/subsection headers depends on whether the
  // column header is present (non-BOM mode only).
  state.body.style.setProperty("--inv-col-header-h", state.bomData ? "0px" : "26px");
  if (state.bomData) {
    var matchedInvKeys = renderBomComparison();
    renderRemainingInventory(matchedInvKeys, (state.searchInput.value || "").toLowerCase());
  } else {
    var headerWrap = document.createElement("div");
    headerWrap.innerHTML = renderInvColHeader({
      groupLevel: state.groupLevel,
      sortColumn: state.sortColumn,
      sortScope: state.sortScope,
      vendorGroupScope: state.vendorGroupScope,
      hideDescs: state.hideDescs,
    });
    while (headerWrap.firstChild) state.body.appendChild(headerWrap.firstChild);
    renderNormalInventory();
  }
  // The header (flex cells or <thead>) was just rebuilt from scratch, so
  // re-stamp the persisted widths onto it. The inventory grid's widths live on
  // the container and survive on their own; the BOM table's live on its fresh
  // <th>s and do not.
  applyColWidths();
  refreshImportMarkers();
  // Restore the pre-rebuild scroll position (see note at top of render()).
  if (state.body.scrollTop !== prevScroll) state.body.scrollTop = prevScroll;
}
