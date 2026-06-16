/* inventory-panel.js — Thin wiring for the inventory panel.
   init(), top-level render(), distributor-filter UI, EventBus wiring.
   Delegates to inv-render.js, inv-bom-mode.js, inv-row-build.js, inv-mutations.js. */

import { store } from '../store.js';
import { countByDistributor } from './inventory-logic.js';
import { renderInvColHeader } from './inventory-renderer.js';
import state from './inv-state.js';
import { setupEvents } from './inv-events.js';
import { renderNormalInventory } from './inv-render.js';
import { renderBomComparison, renderRemainingInventory } from './inv-bom-mode.js';
import { refreshImportMarkers } from './inv-import-markers.js';

// ── Init ──

export function init() {
  state.body = document.getElementById("inventory-body");
  state.searchInput = document.getElementById("inv-search");
  state.clearFilterBtn = document.getElementById("clear-dist-filter");
  state.distFilterBar = document.getElementById("dist-filter-bar");

  // Store render callback in state so extracted modules can trigger re-renders
  state._render = render;

  setupEvents({ render: render, updateDistFilterUI: updateDistFilterUI });

  if (window.ResizeObserver && state.body) {
    new ResizeObserver(() => refreshImportMarkers()).observe(state.body);
  }
}

// ── Distributor filter UI state ──

function updateDistFilterUI() {
  var btns = state.distFilterBar.querySelectorAll(".dist-filter-btn");
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.toggle("active", state.activeDistributors.has(btns[i].dataset.distributor));
  }
  state.clearFilterBtn.disabled = (state.activeDistributors.size === 0 && !state.searchInput.value);
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
  refreshImportMarkers();
}
