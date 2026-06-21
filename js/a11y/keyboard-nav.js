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
// Data column spans (.part-ids, .part-mpn, etc.) are included so arrow keys can traverse
// all visible columns, not just action buttons. roving-grid.js innermost() ensures that
// when a column wrapper and a child button both match, only the child is a grid cell.
const INV_CELLS = [
  // Plain inventory column spans (inv-row-build.js / inventory-renderer.js renderPartRowHtml).
  '.part-ids', '.part-mpn', '.part-vendor', '.part-unit-price', '.part-value',
  '.part-qty', '.part-desc',
  // BOM comparison data cells rendered by createBomRowElement into #inventory-body tbody tr.
  // td.status is the icon column (not interactive); td.btn-group cells are covered by
  // the button selectors below. roving-grid.js innermost() prevents double-counting.
  'td:not(.status):not(.btn-group)',
  // Action buttons (appear in both row types and/or BOM alt-rows).
  '.no-dist-warn', '.price-warn-btn', '.generic-group-badge',
  '.near-miss-badge', '.adj-btn', '.link-btn', '.confirm-btn', '.unconfirm-btn', '.swap-btn',
  '.inv-section-header', '.inv-parent-header', '.inv-subsection-header',
].join(',');

// BOM comparison row cell selectors (confirmed against inventory-renderer.js createBomRowElement).
// The brief guessed rowKey: 'data-bom-key' — the actual attribute is 'data-part-key'.
// The brief included '.row-delete' — that class lives in staging rows only, not main BOM rows.
// '.unconfirm-btn' is included alongside '.confirm-btn' (the renderer emits one or the other).
// '.swap-btn' only appears in alt-rows, which are also <tr> elements matched by rowSelector 'tr'.
// td:not(.status):not(.btn-group) captures all data cells (refs-cell, mono, inv-qty-cell,
// desc-cell) so arrow keys traverse all columns. roving-grid.js innermost() ensures buttons
// inside td.btn-group don't double-count with the td itself (btn-group is excluded anyway).
const BOM_CELLS = [
  'td:not(.status):not(.btn-group)',
  '.swap-btn', '.adj-btn', '.confirm-btn', '.unconfirm-btn', '.link-btn',
].join(',');

const SCROLL_SELECTORS = [
  '.panel-body', '.prefs-sliders', '.vendors-list', '.vendors-detail-col',
  '.bom-table-wrap', '.flyout-members', '.console-entries',
  '.label-export-preview', '.label-po-list', '.import-preview',
  '.scan-grouping-list', '.ocr-grid-pane',
  // NOTE: .refs-scroll intentionally removed — the Designators cell (td.refs-cell)
  // is now a grid cell navigated by arrow keys; making .refs-scroll a separate
  // scroll tab-stop would split focus between two stops in the same column.
];

const ACTIVATE_SELECTORS = [
  '.label-po-row',
  '.inv-section-header', '.inv-parent-header', '.inv-subsection-header',
];

// Inventory row selector includes both part rows and the three header types.
// Headers act as single-cell rows (roving-grid.js grid() handles them via row.matches()).
// 'tbody tr' covers BOM comparison <tr> rows rendered by createBomRowElement so that
// ArrowDown/Up can traverse them; bare 'tr' is intentionally avoided to prevent
// matching table header rows (<thead tr>).
const INV_ROW_SELECTOR = [
  '.inv-part-row',
  'tbody tr',
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
    const invObserver = new MutationObserver(() => {
      if (invRafPending) return;
      invRafPending = true;
      requestAnimationFrame(() => {
        invRafPending = false;
        // Temporarily disconnect to avoid observing our own tabindex mutations.
        invObserver.disconnect();
        rearmInventory();
        invObserver.observe(invBody, { childList: true, subtree: false });
      });
    });
    invObserver.observe(invBody, { childList: true, subtree: false });
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
