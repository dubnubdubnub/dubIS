// @ts-check
/* col-resize-logic.js — the arithmetic and the persistence shape behind
   user-draggable column widths. Pure: no DOM, no imports, no side effects, so
   every rule here is unit-testable (tests/js/col-resize-logic.test.js).
   js/col-resize.js binds it to pointer events and to whatever mechanism a
   given table uses to apply a width.

   ── Units ─────────────────────────────────────────────────────────────────
   Every number in this module is **authored px** — the space of `offsetWidth`
   and of any px value written to a style. Pointer readings (`clientX`) are
   post-zoom and must be divided by the zoom factor *before* they reach these
   functions; js/col-resize.js does that with `toInnerPx()` from ui-zoom.js.
   See the root-zoom trap in CLAUDE.md.

   ── State shape ───────────────────────────────────────────────────────────
     { [tableId]: { [colId]: widthInAuthoredPx } }
   A column absent from the map is at its default: the map records *overrides*
   only, so a later change to a default token still moves every untouched
   column. Persisted as-is (JSON) under the `column_widths` preference. */

/**
 * One resizable column: its identity, its default width, and the floor below
 * which dragging cannot shrink it.
 * @typedef {object} ColSpec
 * @property {string} id     stable column id, also the `data-col-resize` value
 * @property {number} def    default width in authored px (read from a CSS token)
 * @property {number} min    smallest width a drag may produce (authored px)
 * @property {number} [max]  largest width a drag may produce; DEFAULT_MAX_PX if absent
 */

/**
 * A resizable table: an id to persist under, plus its column specs.
 * @typedef {object} TableColSpec
 * @property {string} id
 * @property {ColSpec[]} cols
 */

/** @typedef {Record<string, Record<string, number>>} WidthState */

/** Ceiling applied when a ColSpec declares no `max`. Keeps a runaway drag from
 *  pushing the flexible column out of existence entirely. */
export const DEFAULT_MAX_PX = 1000;

/** Attribute a drag handle carries, valued with its column id. */
export const HANDLE_ATTR = 'data-col-resize';

/** Attribute pairing a handle with its table id — handles from two different
 *  tables can share one delegation root (both of this app's tables render
 *  inside #inventory-body). */
export const HANDLE_TABLE_ATTR = 'data-col-resize-table';

/**
 * The markup for one drag handle, emitted *inside* a header cell: the cell is
 * the positioning context and the handle is out of flow (see
 * css/components/col-resize.css), so adding it cannot disturb column
 * alignment the way an extra flex child would.
 *
 * Lives in this module, not in the DOM-bound one, so the HTML builders can
 * emit handles without importing anything that reaches store.js (whose
 * constants.js import has a top-level await fetch — see CLAUDE.md).
 * @param {string} tableId
 * @param {string} colId
 * @returns {string}
 */
export function colResizeHandleHtml(tableId, colId) {
  return '<span class="col-resize-handle" ' + HANDLE_ATTR + '="' + colId + '" '
    + HANDLE_TABLE_ATTR + '="' + tableId + '" aria-hidden="true"'
    + ' title="Drag to resize · double-click to reset"></span>';
}

/**
 * The spec for one column, or null when the id is unknown to this table.
 * @param {ColSpec[]} cols
 * @param {string} colId
 * @returns {ColSpec|null}
 */
export function findCol(cols, colId) {
  for (const c of cols) if (c.id === colId) return c;
  return null;
}

/**
 * Clamp a candidate width into the column's legal range, rounding to whole px.
 * A non-finite candidate (NaN from a corrupt store, Infinity from bad
 * arithmetic) resolves to the default rather than throwing — a width is never
 * important enough to break a render over.
 * @param {number} px
 * @param {ColSpec} spec
 * @returns {number}
 */
export function clampWidth(px, spec) {
  const max = typeof spec.max === 'number' ? spec.max : DEFAULT_MAX_PX;
  if (!Number.isFinite(px)) return spec.def;
  return Math.min(max, Math.max(spec.min, Math.round(px)));
}

/**
 * Width produced by dragging a column's right edge by `deltaPx` (authored px;
 * negative shrinks). Anchored on the width at drag start, never on the
 * previous frame, so a drag out to the clamp and back returns exactly to where
 * the pointer says it should be instead of accumulating rounding drift.
 * @param {number} startPx width when the drag began
 * @param {number} deltaPx authored-px pointer movement along x
 * @param {ColSpec} spec
 * @returns {number}
 */
export function nextWidth(startPx, deltaPx, spec) {
  const start = Number.isFinite(startPx) ? startPx : spec.def;
  const delta = Number.isFinite(deltaPx) ? deltaPx : 0;
  return clampWidth(start + delta, spec);
}

/**
 * The width a column should render at: its stored override (re-clamped, since
 * the stored value may predate a change to the spec) or its default.
 * @param {WidthState} state
 * @param {string} tableId
 * @param {ColSpec} spec
 * @returns {number}
 */
export function widthFor(state, tableId, spec) {
  const table = state && state[tableId];
  const stored = table && Object.prototype.hasOwnProperty.call(table, spec.id)
    ? table[spec.id] : undefined;
  if (typeof stored !== 'number') return spec.def;
  return clampWidth(stored, spec);
}

/**
 * True when the column carries a user override (i.e. reset would change it).
 * @param {WidthState} state
 * @param {string} tableId
 * @param {string} colId
 * @returns {boolean}
 */
export function isCustom(state, tableId, colId) {
  const table = state && state[tableId];
  return !!table && typeof table[colId] === 'number';
}

/**
 * State with one column's width set (clamped). Immutable: returns a new object
 * so a caller can keep the previous state for comparison.
 * @param {WidthState} state
 * @param {string} tableId
 * @param {ColSpec} spec
 * @param {number} px
 * @returns {WidthState}
 */
export function withWidth(state, tableId, spec, px) {
  const next = serializeWidths(state);
  const table = next[tableId] || (next[tableId] = {});
  table[spec.id] = clampWidth(px, spec);
  return next;
}

/**
 * State with one column's override removed — that column returns to its
 * default. Drops the table entry entirely once its last override is gone, so
 * an untouched table persists nothing.
 * @param {WidthState} state
 * @param {string} tableId
 * @param {string} colId
 * @returns {WidthState}
 */
export function withoutCol(state, tableId, colId) {
  const next = serializeWidths(state);
  const table = next[tableId];
  if (!table) return next;
  delete table[colId];
  if (Object.keys(table).length === 0) delete next[tableId];
  return next;
}

/**
 * State with every override for one table removed (the reset-to-defaults path).
 * @param {WidthState} state
 * @param {string} tableId
 * @returns {WidthState}
 */
export function withoutTable(state, tableId) {
  const next = serializeWidths(state);
  delete next[tableId];
  return next;
}

/**
 * A JSON-safe deep copy — what gets written to preferences, and the copy-on-
 * write primitive the `with*` helpers above build on.
 * @param {WidthState} state
 * @returns {WidthState}
 */
export function serializeWidths(state) {
  /** @type {WidthState} */
  const out = {};
  if (!state || typeof state !== 'object') return out;
  for (const tableId of Object.keys(state)) {
    const table = state[tableId];
    if (!table || typeof table !== 'object') continue;
    /** @type {Record<string, number>} */
    const cols = {};
    for (const colId of Object.keys(table)) {
      if (typeof table[colId] === 'number') cols[colId] = table[colId];
    }
    out[tableId] = cols;
  }
  return out;
}

/**
 * Turn an arbitrary persisted value into usable state. Anything unrecognised —
 * a corrupt JSON string, a number where an object belongs, a table or column
 * this build no longer has, a width outside the column's range — is dropped or
 * clamped rather than thrown, so a bad preferences file degrades to default
 * widths instead of an unrenderable grid.
 * @param {unknown} raw the stored value (object, JSON string, or junk)
 * @param {TableColSpec[]} tables the tables currently registered
 * @returns {WidthState}
 */
export function normalizeWidths(raw, tables) {
  /** @type {WidthState} */
  const out = {};
  /** @type {unknown} */
  let parsed = raw;
  if (typeof raw === 'string') {
    try { parsed = JSON.parse(raw); } catch { return out; }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return out;
  const src = /** @type {Record<string, unknown>} */ (parsed);
  for (const table of (Array.isArray(tables) ? tables : [])) {
    const stored = src[table.id];
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) continue;
    const storedCols = /** @type {Record<string, unknown>} */ (stored);
    /** @type {Record<string, number>} */
    const cols = {};
    for (const spec of table.cols) {
      const v = storedCols[spec.id];
      if (typeof v !== 'number' || !Number.isFinite(v)) continue;
      cols[spec.id] = clampWidth(v, spec);
    }
    if (Object.keys(cols).length > 0) out[table.id] = cols;
  }
  return out;
}
