// @ts-check
/* grid-selection.js — Excel-like grid selection for staging tables.
   Shared by bom-panel.js and import-panel.js. */

import { fillValues } from './grid-fill.js';

/** @type {any} */
const _win = window;

/**
 * @typedef {{
 *   getCellValue: (row: number, col: number) => string,
 *   setCellValue: (row: number, col: number, val: string) => void,
 *   onBeforeChange: () => void,
 *   onCellChange: () => void,
 *   readOnlyCols?: number[],
 *   specialCols?: Record<number, { render: (td: HTMLTableCellElement, val: string) => void }>,
 *   isDisabled?: () => boolean
 * }} GridCallbacks
 */

export class GridSelection {
  /**
   * @param {HTMLElement} wrapperEl — positioned container (needs position:relative)
   * @param {HTMLTableElement} tableEl
   * @param {GridCallbacks} cb
   */
  constructor(wrapperEl, tableEl, cb) {
    this._wrapper = wrapperEl;
    this._table = tableEl;
    this._cb = cb;
    this._readOnly = new Set(cb.readOnlyCols || []);
    this._specialCols = cb.specialCols || {};

    // Build DOM-col ↔ data-col mapping
    this._buildColMap();

    // State
    /** @type {{ r: number, c: number } | null} */
    this._anchor = null;
    /** @type {{ r: number, c: number } | null} */
    this._cursor = null;
    /** @type {{ r1: number, c1: number, r2: number, c2: number } | null} */
    this._range = null;
    /** @type {"select" | "edit"} */
    this._mode = "select";

    // Create overlay elements (all hidden initially via inline style)
    this._sel = this._mkDiv("grid-sel");
    this._sel.style.display = "none";
    this._rangeEl = this._mkDiv("grid-range");
    this._rangeEl.style.display = "none";
    this._fillHandle = this._mkDiv("grid-fill-handle");
    this._fillHandle.style.display = "none";
    this._input = /** @type {HTMLInputElement} */ (document.createElement("input"));
    this._input.className = "grid-edit-input";
    this._input.style.display = "none";
    this._wrapper.appendChild(this._input);
    this._magnifier = this._mkDiv("grid-magnifier");
    this._magnifier.style.display = "none";
    document.body.appendChild(this._magnifier);
    this._fillPreview = this._mkDiv("grid-fill-preview");
    this._fillPreview.style.display = "none";

    // Add grid-table class to table and make wrapper focusable
    this._table.classList.add("grid-table");
    if (!this._wrapper.hasAttribute("tabindex")) {
      this._wrapper.setAttribute("tabindex", "-1");
    }

    // Bind handlers (store refs for cleanup)
    this._onPointerDown = this._handlePointerDown.bind(this);
    this._onDblClick = this._handleDblClick.bind(this);
    this._onKeyDown = this._handleKeyDown.bind(this);
    this._onInputBlur = this._commitEdit.bind(this);
    this._onInputKeyDown = this._handleInputKeyDown.bind(this);
    this._onFillPointerDown = this._handleFillPointerDown.bind(this);
    this._onScroll = this._handleScroll.bind(this);

    this._table.addEventListener("pointerdown", this._onPointerDown);
    this._table.addEventListener("dblclick", this._onDblClick);
    document.addEventListener("keydown", this._onKeyDown);
    this._input.addEventListener("blur", this._onInputBlur);
    this._input.addEventListener("keydown", this._onInputKeyDown);
    this._fillHandle.addEventListener("pointerdown", this._onFillPointerDown);
    this._wrapper.addEventListener("scroll", this._onScroll);

    // Track active grid for undo coordination
    this._onFocusIn = () => { _win._activeGrid = this; };
    this._onFocusOut = (e) => {
      if (!this._wrapper.contains(/** @type {Node} */ (e.relatedTarget))) {
        if (_win._activeGrid === this) _win._activeGrid = null;
      }
    };
    this._wrapper.addEventListener("focusin", this._onFocusIn);
    this._wrapper.addEventListener("focusout", this._onFocusOut);
  }

  // ── Public API ──

  isEditing() { return this._mode === "edit"; }

  destroy() {
    this._table.removeEventListener("pointerdown", this._onPointerDown);
    this._table.removeEventListener("dblclick", this._onDblClick);
    document.removeEventListener("keydown", this._onKeyDown);
    this._input.removeEventListener("blur", this._onInputBlur);
    this._input.removeEventListener("keydown", this._onInputKeyDown);
    this._fillHandle.removeEventListener("pointerdown", this._onFillPointerDown);
    this._wrapper.removeEventListener("scroll", this._onScroll);
    this._wrapper.removeEventListener("focusin", this._onFocusIn);
    this._wrapper.removeEventListener("focusout", this._onFocusOut);
    this._sel.remove();
    this._rangeEl.remove();
    this._fillHandle.remove();
    this._fillPreview.remove();
    this._input.remove();
    this._magnifier.remove();
    this._table.classList.remove("grid-table");
    if (_win._activeGrid === this) _win._activeGrid = null;
  }

  /** Re-select a cell after table rebuild. */
  restoreSelection(r, c) {
    const rows = this._dataRows();
    if (r >= 0 && r < rows.length && c >= 0 && c < this._dataCols) {
      this._cursor = { r, c };
      this._anchor = { r, c };
      this._range = { r1: r, c1: c, r2: r, c2: c };
      this._positionOverlays();
    }
  }

  /** Update a single cell's text content without full rebuild. */
  refreshCell(row, col) {
    const td = this._getTd(row, col);
    if (!td) return;
    const val = this._cb.getCellValue(row, col);
    if (col in this._specialCols) {
      this._specialCols[col].render(td, val);
    } else {
      td.textContent = val;
    }
  }

  // ── Internal helpers ──

  _buildColMap() {
    // _domToData[domIdx] = dataIdx (or -1 if readOnly)
    // _dataToDom[dataIdx] = domIdx
    const firstRow = this._table.querySelector("thead tr");
    const thCount = firstRow ? firstRow.children.length : 0;
    this._domToData = [];
    this._dataToDom = [];
    let dataIdx = 0;
    for (let domIdx = 0; domIdx < thCount; domIdx++) {
      if (this._readOnly.has(domIdx)) {
        this._domToData.push(-1);
      } else {
        this._domToData.push(dataIdx);
        this._dataToDom.push(domIdx);
        dataIdx++;
      }
    }
    this._dataCols = dataIdx;
  }

  _dataRows() {
    return this._table.tBodies[0] ? Array.from(this._table.tBodies[0].rows) : [];
  }

  /** Get the <td> element for a data-space (row, col). */
  _getTd(row, col) {
    const rows = this._dataRows();
    if (row < 0 || row >= rows.length) return null;
    const domCol = this._dataToDom[col];
    if (domCol == null) return null;
    return /** @type {HTMLTableCellElement | null} */ (rows[row].cells[domCol] || null);
  }

  /** Determine data-space (row, col) from a <td> element. Returns null if readOnly. */
  _cellFromTd(td) {
    const tr = td.closest("tr");
    if (!tr || !tr.parentElement || tr.parentElement.tagName !== "TBODY") return null;
    const rows = this._dataRows();
    const row = rows.indexOf(/** @type {HTMLTableRowElement} */ (tr));
    if (row < 0) return null;
    const domCol = Array.from(tr.cells).indexOf(td);
    if (domCol < 0) return null;
    const dataCol = this._domToData[domCol];
    if (dataCol == null || dataCol < 0) return null;
    return { r: row, c: dataCol };
  }

  _mkDiv(cls) {
    const d = document.createElement("div");
    d.className = cls;
    this._wrapper.appendChild(d);
    return d;
  }

  _isDisabled() {
    return this._cb.isDisabled ? this._cb.isDisabled() : false;
  }

  // ── Pointer handlers ──

  _handlePointerDown(e) {
    if (this._isDisabled()) return;
    const td = /** @type {HTMLElement} */ (e.target).closest("td");
    if (!td) return;
    const cell = this._cellFromTd(td);
    if (!cell) return; // readOnly column — let click fall through

    e.preventDefault();
    _win._activeGrid = this;

    if (this._mode === "edit") this._commitEdit();

    if (e.shiftKey && this._anchor) {
      // Extend range
      this._cursor = cell;
      this._range = this._normalizeRange(this._anchor, cell);
    } else {
      this._anchor = cell;
      this._cursor = cell;
      this._range = { r1: cell.r, c1: cell.c, r2: cell.r, c2: cell.c };

      // Start drag-select
      this._dragSelecting = true;
      /** @type {Element} */ (e.target).closest("table")?.setPointerCapture(e.pointerId);
      const onMove = (/** @type {PointerEvent} */ ev) => {
        const el = document.elementFromPoint(ev.clientX, ev.clientY);
        if (!el) return;
        const td2 = el.closest("td");
        if (!td2) return;
        const c2 = this._cellFromTd(td2);
        if (!c2) return;
        this._cursor = c2;
        this._range = this._normalizeRange(this._anchor, c2);
        this._positionOverlays();
      };
      const onUp = () => {
        this._dragSelecting = false;
        this._table.removeEventListener("pointermove", onMove);
        this._table.removeEventListener("pointerup", onUp);
        this._table.removeEventListener("lostpointercapture", onUp);
      };
      this._table.addEventListener("pointermove", onMove);
      this._table.addEventListener("pointerup", onUp);
      this._table.addEventListener("lostpointercapture", onUp);
    }

    this._positionOverlays();
    this._showMagnifier();
  }

  _handleDblClick(e) {
    if (this._isDisabled()) return;
    const td = /** @type {HTMLElement} */ (e.target).closest("td");
    if (!td) return;
    const cell = this._cellFromTd(td);
    if (!cell) return;

    this._cursor = cell;
    this._anchor = cell;
    this._range = { r1: cell.r, c1: cell.c, r2: cell.r, c2: cell.c };
    this._enterEdit(false);
  }

  // ── Keyboard ──

  _handleKeyDown(e) {
    if (this._isDisabled()) return;
    if (this._mode === "edit") return; // handled by _handleInputKeyDown
    if (!this._cursor) return;
    // Only respond when our wrapper/table has focus context
    if (_win._activeGrid !== this) return;

    const { r, c } = this._cursor;
    const rows = this._dataRows();
    const maxR = rows.length - 1;
    const maxC = this._dataCols - 1;

    // Arrow keys
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      let nr = r, nc = c;
      if (e.key === "ArrowDown") nr = Math.min(r + 1, maxR);
      else if (e.key === "ArrowUp") nr = Math.max(r - 1, 0);
      else if (e.key === "ArrowRight") nc = Math.min(c + 1, maxC);
      else if (e.key === "ArrowLeft") nc = Math.max(c - 1, 0);

      this._cursor = { r: nr, c: nc };
      if (e.shiftKey) {
        this._range = this._normalizeRange(this._anchor, this._cursor);
      } else {
        this._anchor = { r: nr, c: nc };
        this._range = { r1: nr, c1: nc, r2: nr, c2: nc };
      }
      this._positionOverlays();
      this._scrollIntoView();
      this._showMagnifier();
      return;
    }

    // Tab / Shift+Tab
    if (e.key === "Tab") {
      e.preventDefault();
      let nc = e.shiftKey ? c - 1 : c + 1;
      let nr = r;
      if (nc > maxC) { nc = 0; nr = Math.min(r + 1, maxR); }
      if (nc < 0) { nc = maxC; nr = Math.max(r - 1, 0); }
      this._cursor = { r: nr, c: nc };
      this._anchor = { r: nr, c: nc };
      this._range = { r1: nr, c1: nc, r2: nr, c2: nc };
      this._positionOverlays();
      this._scrollIntoView();
      this._showMagnifier();
      return;
    }

    // Enter / Shift+Enter
    if (e.key === "Enter") {
      e.preventDefault();
      const nr = e.shiftKey ? Math.max(r - 1, 0) : Math.min(r + 1, maxR);
      this._cursor = { r: nr, c };
      this._anchor = { r: nr, c };
      this._range = { r1: nr, c1: c, r2: nr, c2: c };
      this._positionOverlays();
      this._scrollIntoView();
      this._showMagnifier();
      return;
    }

    // F2 — edit preserving value
    if (e.key === "F2") {
      e.preventDefault();
      this._enterEdit(false);
      return;
    }

    // Delete / Backspace — clear range
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      if (!this._range) return;
      this._cb.onBeforeChange();
      const { r1, c1, r2, c2 } = this._range;
      for (let ri = r1; ri <= r2; ri++) {
        for (let ci = c1; ci <= c2; ci++) {
          this._cb.setCellValue(ri, ci, "");
          this._updateTd(ri, ci, "");
        }
      }
      this._cb.onCellChange();
      return;
    }

    // Escape — clear selection
    if (e.key === "Escape") {
      e.preventDefault();
      this._clearSelection();
      return;
    }

    // Type-to-edit: printable character enters edit mode replacing content
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      this._enterEdit(true, e.key);
      e.preventDefault();
      return;
    }
  }

  _handleInputKeyDown(e) {
    if (e.key === "Tab") {
      e.preventDefault();
      this._commitEdit();
      // Move to next cell
      const { r, c } = this._cursor;
      const maxC = this._dataCols - 1;
      const maxR = this._dataRows().length - 1;
      let nc = e.shiftKey ? c - 1 : c + 1;
      let nr = r;
      if (nc > maxC) { nc = 0; nr = Math.min(r + 1, maxR); }
      if (nc < 0) { nc = maxC; nr = Math.max(r - 1, 0); }
      this._cursor = { r: nr, c: nc };
      this._anchor = { r: nr, c: nc };
      this._range = { r1: nr, c1: nc, r2: nr, c2: nc };
      this._positionOverlays();
      this._scrollIntoView();
      this._showMagnifier();
    } else if (e.key === "Enter") {
      e.preventDefault();
      this._commitEdit();
      const { r, c } = this._cursor;
      const maxR = this._dataRows().length - 1;
      const nr = e.shiftKey ? Math.max(r - 1, 0) : Math.min(r + 1, maxR);
      this._cursor = { r: nr, c };
      this._anchor = { r: nr, c };
      this._range = { r1: nr, c1: c, r2: nr, c2: c };
      this._positionOverlays();
      this._scrollIntoView();
      this._showMagnifier();
    } else if (e.key === "Escape") {
      e.preventDefault();
      this._cancelEdit();
    }
    // Ctrl+Z inside input: let browser handle it (don't propagate to global undo)
    if ((e.ctrlKey || e.metaKey) && e.key === "z") {
      e.stopPropagation();
    }
  }

  // ── Edit mode ──

  /** @param {boolean} replace — if true, clear cell and start with given char */
  _enterEdit(replace, initialChar) {
    if (!this._cursor) return;
    this._mode = "edit";
    this._editOriginal = this._cb.getCellValue(this._cursor.r, this._cursor.c);

    const td = this._getTd(this._cursor.r, this._cursor.c);
    if (!td) return;
    const rect = td.getBoundingClientRect();
    const wrapRect = this._wrapper.getBoundingClientRect();

    this._input.style.display = "";
    this._input.style.left = (rect.left - wrapRect.left + this._wrapper.scrollLeft) + "px";
    this._input.style.top = (rect.top - wrapRect.top + this._wrapper.scrollTop) + "px";
    this._input.style.width = rect.width + "px";
    this._input.style.height = rect.height + "px";

    if (replace && initialChar != null) {
      this._input.value = initialChar;
    } else {
      this._input.value = this._editOriginal;
    }
    this._input.focus();
    if (!replace) {
      this._input.select();
    } else {
      // Place cursor at end
      this._input.setSelectionRange(this._input.value.length, this._input.value.length);
    }

    this._hideMagnifier();
  }

  _commitEdit() {
    if (this._mode !== "edit") return;
    this._mode = "select";
    this._input.style.display = "none";

    if (!this._cursor) return;
    const newVal = this._input.value;
    if (newVal !== this._editOriginal) {
      this._cb.onBeforeChange();
      this._cb.setCellValue(this._cursor.r, this._cursor.c, newVal);
      this._updateTd(this._cursor.r, this._cursor.c, newVal);
      this._cb.onCellChange();
    }
  }

  _cancelEdit() {
    this._mode = "select";
    this._input.style.display = "none";
    // Focus table wrapper to keep keyboard navigation working
    this._wrapper.focus();
  }

  // ── Fill handle ──

  _handleFillPointerDown(e) {
    if (this._isDisabled() || !this._range) return;
    e.preventDefault();
    e.stopPropagation();
    this._fillHandle.setPointerCapture(e.pointerId);

    const startRange = { ...this._range };
    const startY = e.clientY;
    const startX = e.clientX;
    let fillDir = null; // "down" | "right" | null
    let fillEnd = null;

    const onMove = (/** @type {PointerEvent} */ ev) => {
      const dy = ev.clientY - startY;
      const dx = ev.clientX - startX;

      // Determine direction once we move enough
      if (!fillDir) {
        if (Math.abs(dy) > 5) fillDir = "down";
        else if (Math.abs(dx) > 5) fillDir = "right";
        else return;
        document.body.classList.add("grid-filling");
      }

      const el = document.elementFromPoint(ev.clientX, ev.clientY);
      if (!el) return;
      const td = el.closest("td");
      if (!td) return;
      const cell = this._cellFromTd(td);
      if (!cell) return;

      if (fillDir === "down") {
        fillEnd = Math.max(cell.r, startRange.r2);
      } else {
        fillEnd = Math.max(cell.c, startRange.c2);
      }

      // Show fill preview
      this._showFillPreview(startRange, fillDir, fillEnd);
    };

    const onUp = () => {
      document.body.classList.remove("grid-filling");
      this._fillHandle.removeEventListener("pointermove", onMove);
      this._fillHandle.removeEventListener("pointerup", onUp);
      this._fillHandle.removeEventListener("lostpointercapture", onUp);
      this._fillPreview.style.display = "none";

      if (fillDir && fillEnd != null) {
        this._executeFill(startRange, fillDir, fillEnd);
      }
    };

    this._fillHandle.addEventListener("pointermove", onMove);
    this._fillHandle.addEventListener("pointerup", onUp);
    this._fillHandle.addEventListener("lostpointercapture", onUp);
  }

  _showFillPreview(range, dir, end) {
    if (dir === "down") {
      const tdStart = this._getTd(range.r2 + 1, range.c1);
      const tdEnd = this._getTd(end, range.c2);
      if (!tdStart || !tdEnd) { this._fillPreview.style.display = "none"; return; }
      this._positionOverBounds(this._fillPreview, tdStart, tdEnd);
      this._fillPreview.style.display = "";
    } else {
      const tdStart = this._getTd(range.r1, range.c2 + 1);
      const tdEnd = this._getTd(range.r2, end);
      if (!tdStart || !tdEnd) { this._fillPreview.style.display = "none"; return; }
      this._positionOverBounds(this._fillPreview, tdStart, tdEnd);
      this._fillPreview.style.display = "";
    }
  }

  _executeFill(range, dir, end) {
    this._cb.onBeforeChange();

    if (dir === "down") {
      for (let c = range.c1; c <= range.c2; c++) {
        const src = [];
        for (let r = range.r1; r <= range.r2; r++) {
          src.push(this._cb.getCellValue(r, c));
        }
        const count = end - range.r2;
        if (count <= 0) continue;
        const vals = fillValues(src, count);
        for (let i = 0; i < count; i++) {
          this._cb.setCellValue(range.r2 + 1 + i, c, vals[i]);
          this._updateTd(range.r2 + 1 + i, c, vals[i]);
        }
      }
      // Extend selection to filled area
      this._range = { r1: range.r1, c1: range.c1, r2: end, c2: range.c2 };
      this._cursor = { r: end, c: range.c2 };
    } else {
      for (let r = range.r1; r <= range.r2; r++) {
        const src = [];
        for (let c = range.c1; c <= range.c2; c++) {
          src.push(this._cb.getCellValue(r, c));
        }
        const count = end - range.c2;
        if (count <= 0) continue;
        const vals = fillValues(src, count);
        for (let i = 0; i < count; i++) {
          this._cb.setCellValue(r, range.c2 + 1 + i, vals[i]);
          this._updateTd(r, range.c2 + 1 + i, vals[i]);
        }
      }
      this._range = { r1: range.r1, c1: range.c1, r2: range.r2, c2: end };
      this._cursor = { r: range.r2, c: end };
    }

    this._cb.onCellChange();
    this._positionOverlays();
  }

  // ── Overlays ──

  _positionOverlays() {
    if (!this._cursor) {
      this._sel.style.display = "none";
      this._rangeEl.style.display = "none";
      this._fillHandle.style.display = "none";
      return;
    }

    // Active cell border
    const td = this._getTd(this._cursor.r, this._cursor.c);
    if (td) {
      this._positionOver(this._sel, td);
      this._sel.style.display = "";
    }

    // Range overlay
    if (this._range) {
      const { r1, c1, r2, c2 } = this._range;
      const isSingle = r1 === r2 && c1 === c2;

      if (!isSingle) {
        const topLeft = this._getTd(r1, c1);
        const botRight = this._getTd(r2, c2);
        if (topLeft && botRight) {
          this._positionOverBounds(this._rangeEl, topLeft, botRight);
          this._rangeEl.style.display = "";
        }
      } else {
        this._rangeEl.style.display = "none";
      }

      // Fill handle at bottom-right of range
      const brTd = this._getTd(r2, c2);
      if (brTd) {
        const brRect = brTd.getBoundingClientRect();
        const wrapRect = this._wrapper.getBoundingClientRect();
        this._fillHandle.style.left = (brRect.right - wrapRect.left + this._wrapper.scrollLeft - 4) + "px";
        this._fillHandle.style.top = (brRect.bottom - wrapRect.top + this._wrapper.scrollTop - 4) + "px";
        this._fillHandle.style.display = "";
      }
    }
  }

  _positionOver(el, td) {
    const rect = td.getBoundingClientRect();
    const wrapRect = this._wrapper.getBoundingClientRect();
    el.style.left = (rect.left - wrapRect.left + this._wrapper.scrollLeft) + "px";
    el.style.top = (rect.top - wrapRect.top + this._wrapper.scrollTop) + "px";
    el.style.width = rect.width + "px";
    el.style.height = rect.height + "px";
  }

  _positionOverBounds(el, tdStart, tdEnd) {
    const r1 = tdStart.getBoundingClientRect();
    const r2 = tdEnd.getBoundingClientRect();
    const wrapRect = this._wrapper.getBoundingClientRect();
    const left = Math.min(r1.left, r2.left) - wrapRect.left + this._wrapper.scrollLeft;
    const top = Math.min(r1.top, r2.top) - wrapRect.top + this._wrapper.scrollTop;
    const right = Math.max(r1.right, r2.right) - wrapRect.left + this._wrapper.scrollLeft;
    const bottom = Math.max(r1.bottom, r2.bottom) - wrapRect.top + this._wrapper.scrollTop;
    el.style.left = left + "px";
    el.style.top = top + "px";
    el.style.width = (right - left) + "px";
    el.style.height = (bottom - top) + "px";
  }

  _normalizeRange(a, b) {
    return {
      r1: Math.min(a.r, b.r), c1: Math.min(a.c, b.c),
      r2: Math.max(a.r, b.r), c2: Math.max(a.c, b.c),
    };
  }

  _clearSelection() {
    this._cursor = null;
    this._anchor = null;
    this._range = null;
    this._sel.style.display = "none";
    this._rangeEl.style.display = "none";
    this._fillHandle.style.display = "none";
    this._hideMagnifier();
  }

  _scrollIntoView() {
    const td = this._getTd(this._cursor.r, this._cursor.c);
    if (td) td.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  _updateTd(row, col, val) {
    const td = this._getTd(row, col);
    if (!td) return;
    if (col in this._specialCols) {
      this._specialCols[col].render(td, val);
    } else {
      td.textContent = val;
    }
  }

  // ── Magnifier ──

  _showMagnifier() {
    if (!this._cursor) { this._hideMagnifier(); return; }
    const td = this._getTd(this._cursor.r, this._cursor.c);
    if (!td) { this._hideMagnifier(); return; }

    // Only show if content is truncated
    if (td.scrollWidth <= td.clientWidth + 1) {
      this._hideMagnifier();
      return;
    }

    const val = this._cb.getCellValue(this._cursor.r, this._cursor.c);
    if (!val) { this._hideMagnifier(); return; }

    this._magnifier.textContent = val;
    this._magnifier.style.display = "";

    // Position using getBoundingClientRect + viewport clamping
    const rect = td.getBoundingClientRect();
    let top = rect.bottom + 4;
    let left = rect.left;

    const mw = this._magnifier.offsetWidth || 200;
    const mh = this._magnifier.offsetHeight || 30;

    if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
    if (left < 8) left = 8;
    if (top + mh > window.innerHeight - 8) top = rect.top - mh - 4;
    if (top < 8) top = 8;

    this._magnifier.style.left = left + "px";
    this._magnifier.style.top = top + "px";
  }

  _hideMagnifier() {
    this._magnifier.style.display = "none";
  }

  _handleScroll() {
    if (this._mode === "edit") this._commitEdit();
    this._positionOverlays();
    this._hideMagnifier();
  }
}
