/* js/a11y/roving-grid.js — 2D roving-tabindex grid for row-based button groups.
   Tab enters the grid as a single stop; Left/Right move within a row, Up/Down
   move to the same column index in the adjacent row (clamped). */
import { getShortcutPrefs } from '../store.js';

const VIM = { h: 'ArrowLeft', j: 'ArrowDown', k: 'ArrowUp', l: 'ArrowRight' };
const NAV_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']);

/** Pure next-cell computation. `rows` = array of per-row cell counts. */
export function computeTarget(rows, r, c, key) {
  if (key === 'ArrowRight') return c + 1 < rows[r] ? { r, c: c + 1 } : null;
  if (key === 'ArrowLeft')  return c - 1 >= 0 ? { r, c: c - 1 } : null;
  if (key === 'Home') return { r, c: 0 };
  if (key === 'End')  return { r, c: rows[r] - 1 };
  if (key === 'ArrowDown') return r + 1 < rows.length ? { r: r + 1, c: Math.min(c, rows[r + 1] - 1) } : null;
  if (key === 'ArrowUp')   return r - 1 >= 0 ? { r: r - 1, c: Math.min(c, rows[r - 1] - 1) } : null;
  return null;
}

/** Return only the innermost matched cells — drop any cell that contains another matched cell. */
function innermost(cells) {
  return cells.filter((c) => !cells.some((o) => o !== c && c.contains(o)));
}

export function RovingGrid(container, { rowSelector, cellSelector, rowKey }) {
  let lastKey = null; // remembers focused row key across re-render

  function grid() {
    const rowEls = Array.from(container.querySelectorAll(rowSelector));
    return rowEls
      .map((row) => ({
        row,
        // If the row element itself matches cellSelector (e.g. a header acting as a
        // single-cell row), use [row] directly; otherwise collect descendant cells.
        // Apply innermost() to drop outer wrappers when both a container and its
        // child button match cellSelector (e.g. td.refs-cell + .refs-scroll child).
        cells: row.matches(cellSelector)
          ? [row]
          : innermost(Array.from(row.querySelectorAll(cellSelector))),
      }))
      .filter((g) => g.cells.length > 0);
  }

  function setRover(cell) {
    container.querySelectorAll(cellSelector).forEach((el) => { el.tabIndex = -1; });
    if (cell) cell.tabIndex = 0;
  }

  function locate(cell) {
    const g = grid();
    for (let r = 0; r < g.length; r++) {
      const c = g[r].cells.indexOf(cell);
      if (c !== -1) return { g, r, c };
    }
    return null;
  }

  function onKeydown(e) {
    let key = e.key;
    if (getShortcutPrefs().vimNav && VIM[key]) key = VIM[key];
    if (!NAV_KEYS.has(key)) return;
    const cell = e.target.closest(cellSelector);
    if (!cell || !container.contains(cell)) return;
    const loc = locate(cell);
    if (!loc) return;
    const counts = loc.g.map((x) => x.cells.length);
    const t = computeTarget(counts, loc.r, loc.c, key);
    if (!t) return;
    e.preventDefault();
    const target = loc.g[t.r].cells[t.c];
    setRover(target);
    target.focus();
    target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function onFocusin(e) {
    const cell = e.target.closest(cellSelector);
    if (!cell || !container.contains(cell)) return;
    setRover(cell);
    if (rowKey) {
      const row = cell.closest(rowSelector);
      if (row) lastKey = row.getAttribute(rowKey);
    }
  }

  function refresh() {
    const g = grid();
    if (!g.length) return;
    // Re-establish a single tab stop, preferring the previously focused row.
    let target = g[0].cells[0];
    if (lastKey && rowKey) {
      const match = g.find((x) => x.row.getAttribute(rowKey) === lastKey);
      if (match) target = match.cells[0];
    }
    setRover(target);
  }

  container.addEventListener('keydown', onKeydown);
  container.addEventListener('focusin', onFocusin);
  refresh();

  return {
    refresh,
    destroy() {
      container.removeEventListener('keydown', onKeydown);
      container.removeEventListener('focusin', onFocusin);
    },
  };
}
