/* js/a11y/keyboard-nav.js — wire roving grids, scrollable regions, and key
   activation across the app; re-apply after re-renders. */
import { RovingGrid } from './roving-grid.js';
import { makeScrollable } from './scrollable.js';
import { activateOnKey } from './activate-on-key.js';
import { EventBus, Events } from '../event-bus.js';

// Inventory cell selectors (confirmed against inventory-renderer.js / inv-row-build.js).
// Note: .price-warn-btn only exists when qty > 0 and no unit price; .link-btn only in
// BOM mode; .generic-group-badge and .near-miss-badge only when applicable — all are
// optional and the grid gracefully handles rows with fewer cells.
const INV_CELLS = [
  '.part-mpn', '.no-dist-warn', '.price-warn-btn', '.generic-group-badge',
  '.near-miss-badge', '.adj-btn', '.link-btn',
].join(',');

// BOM comparison row cell selectors (confirmed against inventory-renderer.js createBomRowElement).
// The brief guessed rowKey: 'data-bom-key' — the actual attribute is 'data-part-key'.
// The brief included '.row-delete' — that class lives in staging rows only, not main BOM rows.
// '.unconfirm-btn' is included alongside '.confirm-btn' (the renderer emits one or the other).
const BOM_CELLS = [
  '.swap-btn', '.adj-btn', '.confirm-btn', '.unconfirm-btn', '.link-btn',
].join(',');

const SCROLL_SELECTORS = [
  '.panel-body', '.prefs-sliders', '.vendors-list', '.vendors-detail-col',
  '.bom-table-wrap', '.flyout-members', '.refs-scroll', '.console-entries',
  '.label-export-preview', '.label-po-list', '.import-preview',
  '.scan-grouping-list', '.ocr-grid-pane',
];

// Note: inv-section-header / inv-parent-header / inv-subsection-header are
// intentionally excluded. They live inside #inventory-body, are rebuilt on
// every render, and would gain tabindex=0 from activateOnKey — which
// violates the "exactly one tab stop in the inventory grid" rule (the roving
// grid owns that single tab stop). Those headers already have direct click
// listeners and keyboard activation is deferred to a follow-up task.
const ACTIVATE_SELECTORS = [
  '.label-po-row',
];

let invGrid = null;
let bomGrid = null;

function applyScrollables(root = document) {
  SCROLL_SELECTORS.forEach((sel) => root.querySelectorAll(sel).forEach(makeScrollable));
}
function applyActivation(root = document) {
  ACTIVATE_SELECTORS.forEach((sel) => root.querySelectorAll(sel).forEach(activateOnKey));
}

export function initKeyboardNav() {
  const invBody = document.getElementById('inventory-body');
  const bomBody = document.getElementById('bom-body');

  if (invBody) {
    invGrid = RovingGrid(invBody, {
      rowSelector: '.inv-part-row', cellSelector: INV_CELLS, rowKey: 'data-part-id',
    });
  }

  function refreshInventory() {
    if (invGrid) invGrid.refresh();
    applyScrollables();
    applyActivation();
  }
  function refreshBom() {
    if (!bomGrid && bomBody && bomBody.querySelector('#bom-tbody')) {
      const gridRoot = bomBody.querySelector('#bom-tbody');
      bomGrid = RovingGrid(gridRoot, {
        rowSelector: 'tr', cellSelector: BOM_CELLS, rowKey: 'data-part-key',
      });
    } else if (bomGrid) {
      bomGrid.refresh();
    }
    applyScrollables();
  }

  // Re-run refresh on every event that causes inventory-panel render() to run.
  EventBus.on(Events.INVENTORY_LOADED, refreshInventory);
  EventBus.on(Events.INVENTORY_UPDATED, refreshInventory);
  EventBus.on(Events.VENDORS_CHANGED, refreshInventory);
  EventBus.on(Events.PO_CHANGED, refreshInventory);
  EventBus.on(Events.LINKING_MODE, refreshInventory);
  EventBus.on(Events.LABEL_MODE, refreshInventory);
  EventBus.on(Events.LABEL_BULK_SELECTION, refreshInventory);
  EventBus.on(Events.BOM_LOADED, refreshBom);
  EventBus.on(Events.BOM_CLEARED, refreshBom);

  // First pass for anything already in the DOM.
  applyScrollables();
  applyActivation();
}
