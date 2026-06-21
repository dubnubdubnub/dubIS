/* js/a11y/keyboard-nav.js — wire roving grids, scrollable regions, and key
   activation across the app; re-apply after re-renders. */
import { RovingGrid } from './roving-grid.js';
import { makeScrollable } from './scrollable.js';
import { activateOnKey } from './activate-on-key.js';

// Inventory cell selectors (confirmed against inventory-renderer.js / inv-row-build.js).
// Note: .price-warn-btn only exists when qty > 0 and no unit price; .link-btn only in
// BOM mode; .generic-group-badge and .near-miss-badge only when applicable — all are
// optional and the grid gracefully handles rows with fewer cells.
// Header classes (.inv-section-header, .inv-parent-header, .inv-subsection-header) are
// included here so they act as single-cell rows in the roving grid (see Fix 1 in
// roving-grid.js grid()). activateOnKey is idempotent and won't fight the grid because
// once the grid sets tabindex=-1 the element already has a tabindex attribute and
// activateOnKey leaves it alone.
const INV_CELLS = [
  '.part-mpn', '.no-dist-warn', '.price-warn-btn', '.generic-group-badge',
  '.near-miss-badge', '.adj-btn', '.link-btn',
  '.inv-section-header', '.inv-parent-header', '.inv-subsection-header',
].join(',');

// BOM comparison row cell selectors (confirmed against inventory-renderer.js createBomRowElement).
// The brief guessed rowKey: 'data-bom-key' — the actual attribute is 'data-part-key'.
// The brief included '.row-delete' — that class lives in staging rows only, not main BOM rows.
// '.unconfirm-btn' is included alongside '.confirm-btn' (the renderer emits one or the other).
// '.swap-btn' only appears in alt-rows, which are also <tr> elements matched by rowSelector 'tr'.
const BOM_CELLS = [
  '.swap-btn', '.adj-btn', '.confirm-btn', '.unconfirm-btn', '.link-btn',
].join(',');

const SCROLL_SELECTORS = [
  '.panel-body', '.prefs-sliders', '.vendors-list', '.vendors-detail-col',
  '.bom-table-wrap', '.flyout-members', '.refs-scroll', '.console-entries',
  '.label-export-preview', '.label-po-list', '.import-preview',
  '.scan-grouping-list', '.ocr-grid-pane',
];

const ACTIVATE_SELECTORS = [
  '.label-po-row',
  '.inv-section-header', '.inv-parent-header', '.inv-subsection-header',
];

// Inventory row selector includes both part rows and the three header types.
// Headers act as single-cell rows (roving-grid.js grid() handles them via row.matches()).
const INV_ROW_SELECTOR = [
  '.inv-part-row',
  '.inv-section-header', '.inv-parent-header', '.inv-subsection-header',
].join(',');

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
      rowSelector: INV_ROW_SELECTOR, cellSelector: INV_CELLS, rowKey: 'data-part-id',
    });
  }

  function rearmInventory() {
    // Apply activation and scrollables BEFORE refresh() so the grid's setRover
    // runs last and is authoritative for tabindex (exactly one tab stop).
    applyActivation();
    applyScrollables();
    if (invGrid) invGrid.refresh();
  }

  function rearmBom() {
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

  // MutationObserver on #inventory-body — catches ALL re-render sources:
  // EventBus events, flyout changes, ResizeObserver-triggered renders, etc.
  // rAF debounce coalesces burst mutations; re-entrancy guard prevents loops.
  if (invBody) {
    let invRafPending = false;
    let invObserving = false;
    const invObserver = new MutationObserver(() => {
      if (invRafPending) return;
      invRafPending = true;
      requestAnimationFrame(() => {
        invRafPending = false;
        // Temporarily disconnect to avoid observing our own tabindex mutations.
        invObserver.disconnect();
        invObserving = false;
        rearmInventory();
        invObserver.observe(invBody, { childList: true, subtree: false });
        invObserving = true;
      });
    });
    invObserver.observe(invBody, { childList: true, subtree: false });
    invObserving = true; // eslint-disable-line no-unused-vars
  }

  // MutationObserver on #bom-body — lazy-init BOM grid on first rows, then re-arm.
  if (bomBody) {
    let bomRafPending = false;
    const bomObserver = new MutationObserver(() => {
      if (bomRafPending) return;
      bomRafPending = true;
      requestAnimationFrame(() => {
        bomRafPending = false;
        rearmBom();
      });
    });
    bomObserver.observe(bomBody, { childList: true, subtree: true });
  }

  // First pass for anything already in the DOM.
  applyScrollables();
  applyActivation();
  if (invGrid) invGrid.refresh();
}
