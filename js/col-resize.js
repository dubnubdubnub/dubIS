// @ts-check
/* col-resize.js — user-draggable column widths, for any table shape.

   One mechanism serves both table flavours in this app because a table only
   has to declare *how* a width is applied:

     • the inventory grid is flex rows, not a <table>, and its header and its
       data rows read the same CSS custom property (--inv-col-pn-w and friends),
       so its `apply` writes that property on the grid container — header and
       rows move together, by construction, with no second write to forget.
     • the BOM comparison table is a real <table> with `table-layout: fixed`,
       so its `apply` writes the matching <th>'s inline width.

   Everything else — hit-testing the handle, the drag arithmetic, the clamps,
   persistence, reset — is shared. Pure arithmetic lives in col-resize-logic.js;
   this module is the DOM/pointer binding (the split used by ui-zoom.js and
   panel-collapse.js).

   ── Coordinate spaces ─────────────────────────────────────────────────────
   `event.clientX` is post-zoom; the width we write is authored px. A pointer
   delta therefore goes through `toInnerPx()` before it is added to a width, or
   the column would move by the zoom factor rather than with the cursor (the
   root-zoom trap in CLAUDE.md). No rect is read here at all: the drag anchors
   on the column's *authored* width, which is the number we own.

   ── Where widths live ─────────────────────────────────────────────────────
   In server preferences (`data/preferences.json`, key `column_widths`), next to
   ui_zoom and panels_collapsed. Not localStorage: the desktop app binds an
   ephemeral loopback port per launch (app.pyw's _free_port), so its origin —
   and therefore any localStorage — changes on every start, and a remote client
   would get a different set again. Preferences follow the user instead.

   store.js's loader carries `column_widths` through explicitly (like ui_zoom's
   pass-through) — it copies known keys only, and savePreferences() posts the
   whole in-memory object, so a key the loader ignored would be erased from
   preferences.json by the next unrelated save.

   Panels mount *before* preferences load, so the stored widths are applied from
   app-init.js once loadPreferences() has resolved — the same seam
   applyStoredZoom and applyStoredCollapse use. This module deliberately does
   not fetch preferences itself: an extra GET during panel init reordered
   startup enough to push the roving grid's rAF re-arm (js/a11y/keyboard-nav.js)
   past the frame the win11 E2E leg samples. */

import { AppLog } from './api.js';
import { store, savePreferences } from './store.js';
import { toInnerPx } from './ui-zoom.js';
import {
  findCol, nextWidth, widthFor, withWidth, withoutCol, withoutTable,
  normalizeWidths, serializeWidths, isCustom, HANDLE_ATTR, HANDLE_TABLE_ATTR,
} from './col-resize-logic.js';

/** Preference key. Free-form dict server-side (server/routes/preferences.py). */
const PREF_KEY = 'column_widths';

/**
 * A table that opts into column resizing.
 * @typedef {object} ResizableTable
 * @property {string} id                       persistence + handle key
 * @property {import('./col-resize-logic.js').ColSpec[]} cols
 * @property {() => Element|null} container    persistent delegation root (survives re-render)
 * @property {(colId: string, px: number) => void} apply  write one column's width
 * @property {string} [resetSelector]          control whose click resets every column
 */

/** @type {Map<string, ResizableTable>} */
const _tables = new Map();

/** @type {import('./col-resize-logic.js').WidthState} */
let _state = {};

let _listenersBound = false;
/** Set at pointerup so the click that follows a drag does not also sort. */
let _swallowClick = false;

/** @returns {import('./col-resize-logic.js').TableColSpec[]} */
function tableSpecs() {
  return Array.from(_tables.values()).map(t => ({ id: t.id, cols: t.cols }));
}

/* store.preferences is typed from its literal default, which has no
 * column_widths key; this is the same "free-form bag" the server treats it as. */
function prefsBag() {
  return /** @type {Record<string, unknown>} */ (/** @type {unknown} */ (store.preferences));
}

/**
 * Register a table (idempotent per id) and apply whatever widths are known.
 * Safe to call before or after loadPersistedColWidths().
 * @param {ResizableTable} table
 */
export function registerColResizeTable(table) {
  _tables.set(table.id, table);
  bindListeners();
  applyColWidths(table.id);
}

/** The live width state — read-only view for tests and diagnostics. */
export function colWidthState() {
  return serializeWidths(_state);
}

/**
 * Write every registered column's current width (stored override or default).
 * Call after a re-render that rebuilt a table's header.
 * @param {string} [tableId] limit to one table
 */
export function applyColWidths(tableId) {
  for (const t of _tables.values()) {
    if (tableId && t.id !== tableId) continue;
    for (const spec of t.cols) {
      try {
        t.apply(spec.id, widthFor(_state, t.id, spec));
      } catch (err) {
        AppLog.warn('col-resize: could not apply width for ' + t.id + '.' + spec.id + ' — ' + err);
      }
    }
    markCustomHandles(t);
  }
}

/**
 * Flag the handles of columns carrying an override, so a customised edge stays
 * findable (css/components/col-resize.css keeps its grip faintly visible).
 * @param {ResizableTable} t
 */
function markCustomHandles(t) {
  const container = t.container();
  if (!container) return;
  const handles = container.querySelectorAll('[' + HANDLE_TABLE_ATTR + '="' + t.id + '"]');
  for (const h of handles) {
    const colId = h.getAttribute(HANDLE_ATTR);
    if (colId && isCustom(_state, t.id, colId)) h.setAttribute('data-col-resize-custom', '1');
    else h.removeAttribute('data-col-resize-custom');
  }
}

/**
 * Apply the persisted widths. Synchronous and fetch-free — app-init.js calls
 * this after loadPreferences() resolves, since panels mount before preferences
 * are read. Never throws: a missing, malformed, or stale-shaped stored value
 * falls back to the default widths.
 * @param {unknown} [raw] stored value; defaults to the in-memory preferences
 */
export function applyStoredColWidths(raw) {
  _state = normalizeWidths(raw === undefined ? prefsBag()[PREF_KEY] : raw, tableSpecs());
  applyColWidths();
}

/**
 * Drop every override for one table — its columns return to their token
 * defaults — then persist and re-apply.
 * @param {string} tableId
 */
export function resetColWidths(tableId) {
  const table = _tables.get(tableId);
  if (!table) return;
  if (!table.cols.some(c => isCustom(_state, tableId, c.id))) return;
  _state = withoutTable(_state, tableId);
  persist();
  applyColWidths(tableId);
}

/**
 * Drop one column's override.
 * @param {string} tableId
 * @param {string} colId
 */
export function resetColWidth(tableId, colId) {
  if (!isCustom(_state, tableId, colId)) return;
  _state = withoutCol(_state, tableId, colId);
  persist();
  applyColWidths(tableId);
}

function persist() {
  try {
    prefsBag()[PREF_KEY] = serializeWidths(_state);
    // Fire-and-forget, but never as an unhandled rejection: a width that fails
    // to save is a warning, not a broken grid.
    Promise.resolve(savePreferences()).catch((err) => {
      AppLog.warn('col-resize: could not save column widths — ' + err);
    });
  } catch (err) {
    AppLog.warn('col-resize: could not persist column widths — ' + err);
  }
}

// ── Pointer binding ─────────────────────────────────────────────────────────

/**
 * Resolve the handle under an event to its table + column spec.
 * @param {Event} e
 */
function resolveTarget(e) {
  const target = /** @type {Element|null} */ (e.target);
  if (!target || typeof target.closest !== 'function') return null;
  const handle = target.closest('[' + HANDLE_ATTR + ']');
  if (!handle) return null;
  const tableId = handle.getAttribute(HANDLE_TABLE_ATTR);
  const colId = handle.getAttribute(HANDLE_ATTR);
  if (!tableId || !colId) return null;
  const table = _tables.get(tableId);
  if (!table) return null;
  const spec = findCol(table.cols, colId);
  if (!spec) return null;
  // Only handles that live inside their table's own container are live, so a
  // stale header left in the DOM by another panel cannot drive a resize.
  const container = table.container();
  if (!container || !container.contains(handle)) return null;
  return { table, spec };
}

/** @param {PointerEvent} e */
function onPointerDown(e) {
  if (e.button !== 0) return;
  const hit = resolveTarget(e);
  if (!hit) return;
  const { table, spec } = hit;

  // Suppress the header button's own behaviour (sort/group cycling), focus, and
  // text selection — this press is a resize, not a click.
  e.preventDefault();
  e.stopPropagation();

  const startClientX = e.clientX;                       // post-zoom
  const startPx = widthFor(_state, table.id, spec);     // authored
  let live = startPx;
  document.documentElement.classList.add('col-resizing');

  /** @param {PointerEvent} ev */
  const onMove = (ev) => {
    // clientX is post-zoom; toInnerPx puts the delta in the same space as the
    // width we are about to write.
    live = nextWidth(startPx, toInnerPx(ev.clientX - startClientX), spec);
    try {
      table.apply(spec.id, live);
    } catch (err) {
      AppLog.warn('col-resize: could not apply width mid-drag — ' + err);
    }
  };

  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    document.documentElement.classList.remove('col-resizing');
    // mouseup → click are dispatched in the same task, so a timeout queued
    // here runs after the click we want to swallow.
    _swallowClick = true;
    setTimeout(() => { _swallowClick = false; }, 0);
    if (live !== startPx) {
      _state = withWidth(_state, table.id, spec, live);
      persist();
    }
    applyColWidths(table.id);
  };

  // Window-level, not pointer capture: the header can be rebuilt mid-drag
  // (any re-render replaces the handle element), which would silently drop
  // capture-bound listeners.
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
}

/** @param {MouseEvent} e */
function onClickCapture(e) {
  // A click on a handle, or the click that closes a drag, must not reach the
  // header button underneath (which sorts / cycles grouping).
  if (_swallowClick || resolveTarget(e)) {
    e.stopPropagation();
    e.preventDefault();
    return;
  }
  const target = /** @type {Element|null} */ (e.target);
  if (!target || typeof target.closest !== 'function') return;
  for (const t of _tables.values()) {
    if (!t.resetSelector) continue;
    const container = t.container();
    if (!container || !container.contains(target)) continue;
    if (target.closest(t.resetSelector)) resetColWidths(t.id);
  }
}

/** @param {MouseEvent} e */
function onDblClick(e) {
  const hit = resolveTarget(e);
  if (!hit) return;
  e.stopPropagation();
  e.preventDefault();
  resetColWidth(hit.table.id, hit.spec.id);
}

/* Document-level delegation: both table headers are rebuilt from scratch on
   every render, so per-element listeners would have to be re-bound each time. */
function bindListeners() {
  if (_listenersBound) return;
  _listenersBound = true;
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('click', onClickCapture, true);
  document.addEventListener('dblclick', onDblClick, true);
}
