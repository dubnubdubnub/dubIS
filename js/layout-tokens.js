// @ts-check
/* layout-tokens.js — Single source of truth for layout values shared between CSS and JS.
   CSS custom properties are declared in css/tokens.css and consumed here via
   getComputedStyle so that JS callers never hard-code pixel values. */

/**
 * Read a CSS custom property from :root and return its string value (trimmed).
 * @param {string} name - CSS custom property name, e.g. "--inv-col-pn-w"
 * @returns {string}
 */
export function getLayoutToken(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Read a CSS custom property from :root and parse it as an integer px value.
 * @param {string} name - CSS custom property name, e.g. "--inv-col-pn-w"
 * @returns {number}
 */
export function getLayoutTokenPx(name) {
  return parseInt(getLayoutToken(name), 10);
}

// ── Inventory column width accessors ─────────────────────────────────────────
// Cached at module-init time (document is ready when ES modules execute).

/** Width of the Group / drag-handle column (px). */
export var INV_COL_GROUP_W     = getLayoutTokenPx("--inv-col-group-w");

/** Width of the Part Number (vendor IDs) column (px). */
export var INV_COL_PN_W        = getLayoutTokenPx("--inv-col-pn-w");

/** Width of the Manufacturer PN column (px). */
export var INV_COL_MFGPN_W    = getLayoutTokenPx("--inv-col-mfgpn-w");

/** Width of the purchase-source vendor favicon column (px). */
export var INV_COL_VENDOR_W   = getLayoutTokenPx("--inv-col-vendor-w");

/** Width of the Unit Price column (px). */
export var INV_COL_UNIT_W      = getLayoutTokenPx("--inv-col-unit-w");

/** Width of the Extended (total) Price column (px). */
export var INV_COL_EXTPRICE_W  = getLayoutTokenPx("--inv-col-extprice-w");

/** Width of the Stock Qty column (px). */
export var INV_COL_STOCK_W     = getLayoutTokenPx("--inv-col-stock-w");

// ── Sticky action-button column geometry ─────────────────────────────────────
// These govern the Adjust / Confirm / Link buttons that stick to the right
// edge of the BOM comparison table.

/** Width of the sticky button column — th.btn-group-hdr / td.btn-group (px). */
export var STICKY_BTN_COL_W    = getLayoutTokenPx("--sticky-btn-col-w");

/** Spacing between adjacent buttons inside td.btn-group (px). */
export var STICKY_BTN_GAP      = getLayoutTokenPx("--sticky-btn-gap");

/** Min-width of the inventory column-header ↺ reset button (px), matching .part-actions width. */
export var INV_COL_RESET_MIN_W = getLayoutTokenPx("--inv-col-reset-min-w");

/** Gap between buttons in .part-actions on non-BOM inventory rows (px). */
export var PART_ACTIONS_GAP    = getLayoutTokenPx("--part-actions-gap");

// ── BOM / staging table shared column widths ──────────────────────────────────

/** Width of the status icon column in BOM comparison and staging tables (px). */
export var BOM_STATUS_COL_W    = getLayoutTokenPx("--bom-status-col-w");

/** Width of the delete-row (×) button column in BOM and import staging tables (px). */
export var BOM_ROW_DELETE_W    = getLayoutTokenPx("--bom-row-delete-w");

// ── Group-flyout panel geometry ───────────────────────────────────────────────

/** Default flyout panel width (px). */
export var FLYOUT_W            = getLayoutTokenPx("--flyout-w");

/** Default flyout panel max-height (px). */
export var FLYOUT_MAX_H        = getLayoutTokenPx("--flyout-max-h");

/** Width of the saved-searches sidebar inside the flyout (px). */
export var FLYOUT_SAVED_TABS_W = getLayoutTokenPx("--flyout-saved-tabs-w");

/** Gap between flyout and source row, and between stacked flyouts (px). */
export var FLYOUT_GAP_PX       = getLayoutTokenPx("--flyout-gap");
