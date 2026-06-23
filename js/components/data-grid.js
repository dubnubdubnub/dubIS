// @ts-check
/**
 * js/components/data-grid.js — Reusable keyboard-navigable table component.
 *
 * Exports:
 *   DataGrid(root, opts) → { el, render(data), refresh(), getData(), destroy() }
 *
 * Builds markup with the html/escapeHtml primitives from js/dom/html.js.
 * Uses js/dom/delegate.js for delegated click handling (clean teardown).
 * Integrates js/a11y/roving-grid.js for arrow-key navigation.
 */

import { on } from '../dom/delegate.js';
import { RovingGrid } from '../a11y/roving-grid.js';
import { AppLog } from '../api.js';

/**
 * @typedef {{
 *   key: string,
 *   label: string,
 *   width?: string,
 *   align?: 'left'|'right'|'center',
 *   mono?: boolean,
 *   headerClass?: string,
 *   cellClass?: string,
 *   render?: (item: any, idx: number) => string|Node,
 *   editable?: boolean,
 *   editType?: 'text'|'number'
 * }} ColumnDef
 */

/**
 * @typedef {{
 *   key: string,
 *   label?: string,
 *   icon?: string|Node,
 *   title?: string,
 *   class?: string,
 *   when?: (item: any) => boolean,
 *   onClick: (item: any, ev: Event) => void
 * }} RowAction
 */

/**
 * @typedef {{
 *   columns: ColumnDef[],
 *   rowKey: (item: any, idx: number) => string,
 *   getRowClass?: (item: any) => string,
 *   rowActions?: RowAction[],
 *   onCellEdit?: (item: any, columnKey: string, newValue: string, idx: number) => Promise<void>,
 *   grouping?: {
 *     by: (item: any) => string,
 *     header: (key: string, items: any[]) => string|Node,
 *     collapsible?: boolean,
 *     collapsedByDefault?: boolean
 *   },
 *   footerAggregates?: Array<{ column: string, render: (items: any[]) => string|Node }>,
 *   emptyMessage?: string,
 *   rovingNav?: boolean
 * }} DataGridOptions
 */

/**
 * Create a reusable DataGrid.
 *
 * @param {Element} root — container element the table is appended into
 * @param {DataGridOptions} opts
 * @returns {{ el: HTMLTableElement, render(data: any[]): void, refresh(): void, getData(): any[], destroy(): void }}
 */
export function DataGrid(root, opts) {
  const {
    columns,
    rowKey,
    getRowClass,
    rowActions = [],
    onCellEdit,
    grouping,
    footerAggregates,
    emptyMessage = '',
    rovingNav = true,
  } = opts;

  /** @type {any[]} */
  let data = [];

  /** @type {{ refresh(): void, destroy(): void }|null} */
  let rovingGrid = null;

  /** Delegate listener removers — collected for destroy(). */
  const removers = [];

  // ── Build skeleton table ────────────────────────────────────────────────────

  const table = document.createElement('table');
  table.className = 'data-grid';

  // <thead>
  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  for (const col of columns) {
    const th = document.createElement('th');
    th.textContent = col.label;
    if (col.headerClass) th.className = col.headerClass;
    if (col.width) th.style.width = col.width;
    headerRow.appendChild(th);
  }
  if (rowActions.length > 0) {
    const th = document.createElement('th');
    th.className = 'col-actions';
    headerRow.appendChild(th);
  }
  thead.appendChild(headerRow);
  table.appendChild(thead);

  // <tbody>
  const tbody = document.createElement('tbody');
  table.appendChild(tbody);

  // <tfoot> placeholder (added only when footerAggregates has entries)
  let tfoot = null;
  if (footerAggregates && footerAggregates.length > 0) {
    tfoot = document.createElement('tfoot');
    table.appendChild(tfoot);
  }

  root.appendChild(table);

  // ── Cell edit helpers ───────────────────────────────────────────────────────

  /**
   * Activate inline edit mode for a cell.
   * @param {HTMLTableCellElement} td
   * @param {any} item
   * @param {ColumnDef} col
   * @param {number} idx
   */
  function activateEdit(td, item, col, idx) {
    // Already editing?
    if (td.querySelector('input')) return;

    const originalValue = String(item[col.key] ?? '');
    const input = document.createElement('input');
    input.type = col.editType || 'text';
    input.value = originalValue;
    td.textContent = '';
    td.appendChild(input);
    input.focus();

    function restore(newValue) {
      td.textContent = newValue;
    }

    async function commit() {
      const newValue = input.value;
      // Keep input visible in a pending/disabled state while the async call is in flight
      input.disabled = true;
      if (onCellEdit) {
        try {
          await onCellEdit(item, col.key, newValue, idx);
          // SUCCESS: restore display with new value
          restore(newValue);
        } catch (err) {
          // REJECTION: roll back to original value and surface the error
          restore(originalValue);
          AppLog.error('DataGrid onCellEdit failed: ' + (err && err.message ? err.message : String(err)));
        }
      } else {
        restore(newValue);
      }
    }

    function cancel() {
      restore(originalValue);
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        commit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        cancel();
      }
    });
  }

  // ── Delegated event binding ─────────────────────────────────────────────────

  // Action button clicks
  if (rowActions.length > 0) {
    const removeActionClick = on(table, 'click', '[data-action-key]', (ev, btn) => {
      const actionKey = /** @type {HTMLElement} */ (btn).dataset.actionKey;
      const tr = btn.closest('tr[data-row-key]');
      if (!tr) return;
      const rk = /** @type {HTMLElement} */ (tr).dataset.rowKey;
      const item = data.find((d, i) => rowKey(d, i) === rk);
      if (!item) return;
      const action = rowActions.find(a => a.key === actionKey);
      if (!action) return;
      if (action.when && !action.when(item)) return;
      action.onClick(item, ev);
    });
    removers.push(removeActionClick);
  }

  // Editable cell clicks
  if (onCellEdit) {
    const removeEditClick = on(table, 'click', 'td[data-col-key][data-editable]', (ev, td) => {
      const colKey = /** @type {HTMLElement} */ (td).dataset.colKey;
      const tr = td.closest('tr[data-row-key]');
      if (!tr) return;
      const rk = /** @type {HTMLElement} */ (tr).dataset.rowKey;
      let foundItem = null;
      let foundIdx = -1;
      for (let i = 0; i < data.length; i++) {
        if (rowKey(data[i], i) === rk) {
          foundItem = data[i];
          foundIdx = i;
          break;
        }
      }
      if (!foundItem) return;
      const col = columns.find(c => c.key === colKey);
      if (!col) return;
      activateEdit(/** @type {HTMLTableCellElement} */ (td), foundItem, col, foundIdx);
    });
    removers.push(removeEditClick);
  }

  // ── Render helpers ──────────────────────────────────────────────────────────

  /**
   * Build a data <tr> for a single item.
   * @param {any} item
   * @param {number} idx
   * @returns {HTMLTableRowElement}
   */
  function buildRow(item, idx) {
    const tr = document.createElement('tr');
    tr.dataset.rowKey = rowKey(item, idx);

    if (getRowClass) {
      const cls = getRowClass(item);
      if (cls) tr.className = cls;
    }

    for (const col of columns) {
      const td = document.createElement('td');
      td.dataset.colKey = col.key;
      if (col.editable) td.dataset.editable = '1';
      if (col.cellClass) td.className = col.cellClass;
      if (col.align) td.style.textAlign = col.align;
      if (col.mono) td.style.fontFamily = 'monospace';

      if (col.render) {
        const result = col.render(item, idx);
        if (result instanceof Node) {
          // Caller returned a live Node — insert it directly
          td.appendChild(result);
        } else {
          // Caller returned a plain string — set as text (automatically escaped)
          td.textContent = String(result);
        }
      } else {
        td.textContent = String(item[col.key] ?? '');
      }

      tr.appendChild(td);
    }

    // Actions cell
    if (rowActions.length > 0) {
      const td = document.createElement('td');
      td.className = 'cell-actions';
      for (const action of rowActions) {
        if (action.when && !action.when(item)) continue;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.dataset.actionKey = action.key;
        if (action.label) btn.textContent = action.label;
        // action.icon is trusted caller-provided markup (like raw() — may contain SVG/HTML).
        // Use innerHTML directly; never escape it or it will render as literal text.
        else if (action.icon) {
          if (action.icon instanceof Node) {
            btn.appendChild(action.icon);
          } else {
            btn.innerHTML = action.icon;
          }
        }
        if (action.title) btn.title = action.title;
        if (action.class) btn.className = action.class;
        td.appendChild(btn);
      }
      tr.appendChild(td);
    }

    return tr;
  }

  /**
   * Rebuild tbody (and tfoot) from current data.
   */
  function rebuildBody() {
    tbody.textContent = '';

    if (data.length === 0) {
      if (emptyMessage) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = columns.length + (rowActions.length > 0 ? 1 : 0);
        td.className = 'empty-message';
        td.textContent = emptyMessage;
        tr.appendChild(td);
        tbody.appendChild(tr);
      }
    } else if (grouping) {
      // Partition data into groups preserving insertion order
      /** @type {Map<string, { key: string, items: any[], indices: number[] }>} */
      const groups = new Map();
      data.forEach((item, idx) => {
        const gk = grouping.by(item);
        if (!groups.has(gk)) groups.set(gk, { key: gk, items: [], indices: [] });
        const g = groups.get(gk);
        g.items.push(item);
        g.indices.push(idx);
      });

      const collapsed = grouping.collapsible && grouping.collapsedByDefault;

      for (const [gk, group] of groups) {
        // Group header row
        const headerTr = document.createElement('tr');
        headerTr.className = 'group-header';
        if (grouping.collapsible) {
          headerTr.dataset.groupKey = gk;
          headerTr.dataset.collapsed = collapsed ? '1' : '0';
          headerTr.style.cursor = 'pointer';
        }
        const headerTd = document.createElement('td');
        headerTd.colSpan = columns.length + (rowActions.length > 0 ? 1 : 0);
        const headerContent = grouping.header(gk, group.items);
        if (headerContent instanceof Node) {
          headerTd.appendChild(headerContent);
        } else {
          headerTd.textContent = String(headerContent);
        }
        headerTr.appendChild(headerTd);
        tbody.appendChild(headerTr);

        // Data rows
        for (let gi = 0; gi < group.items.length; gi++) {
          const item = group.items[gi];
          const globalIdx = group.indices[gi];
          const tr = buildRow(item, globalIdx);
          tr.dataset.groupKey = gk;
          if (collapsed) tr.style.display = 'none';
          tbody.appendChild(tr);
        }
      }

      // Wire collapsible header clicks
      if (grouping.collapsible) {
        const removeGroupClick = on(tbody, 'click', 'tr.group-header', (_ev, headerTr) => {
          const gk = /** @type {HTMLElement} */ (headerTr).dataset.groupKey;
          const isCollapsed = /** @type {HTMLElement} */ (headerTr).dataset.collapsed === '1';
          const newCollapsed = !isCollapsed;
          /** @type {HTMLElement} */ (headerTr).dataset.collapsed = newCollapsed ? '1' : '0';
          // Walk children directly to avoid needing CSS.escape (keys may contain special chars)
          const memberRows = Array.from(tbody.children).filter(
            (el) => el !== headerTr &&
              /** @type {HTMLElement} */ (el).dataset.groupKey === gk &&
              !el.classList.contains('group-header'),
          );
          for (const row of memberRows) {
            /** @type {HTMLElement} */ (row).style.display = newCollapsed ? 'none' : '';
          }
        });
        // Store the remover on the tbody's dataset so we can clean up on re-render
        // We push it to removers; but re-render will rebuild tbody, removing old listeners.
        // Since we use on(tbody, ...) and tbody is recreated on each render by clearing
        // textContent, the listener would stay on the same tbody element. We must remove it
        // before re-render. Use a weak pattern: store on the remover list keyed by generation.
        removers.push(removeGroupClick);
        // We mark this remover as group-click so we can selectively clean it pre-render.
        // Actually simplest: just add to removers and remove in destroy(). Group collapse
        // listeners re-bind each render() so duplicates could accumulate. Track separately.
        _groupClickRemovers.push(removeGroupClick);
      }
    } else {
      data.forEach((item, idx) => {
        tbody.appendChild(buildRow(item, idx));
      });
    }

    // Rebuild tfoot
    if (tfoot && footerAggregates && footerAggregates.length > 0) {
      tfoot.textContent = '';
      const tr = document.createElement('tr');
      tr.className = 'footer-row';
      for (const col of columns) {
        const td = document.createElement('td');
        const agg = footerAggregates.find(a => a.column === col.key);
        if (agg) {
          const result = agg.render(data);
          if (result instanceof Node) {
            td.appendChild(result);
          } else {
            td.textContent = String(result);
          }
        }
        tr.appendChild(td);
      }
      if (rowActions.length > 0) {
        tr.appendChild(document.createElement('td'));
      }
      tfoot.appendChild(tr);
    }
  }

  // Track group-click removers separately so re-render can clean them up
  /** @type {Array<() => void>} */
  const _groupClickRemovers = [];

  // ── RovingGrid ──────────────────────────────────────────────────────────────

  function initRoving() {
    if (!rovingNav) return;
    if (rovingGrid) {
      rovingGrid.destroy();
      rovingGrid = null;
    }
    rovingGrid = RovingGrid(tbody, {
      rowSelector: 'tr[data-row-key]',
      cellSelector: 'td',
      rowKey: 'data-row-key',
    });
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  function render(newData) {
    data = newData;
    // Clean up old group-click listeners before rebuilding tbody
    while (_groupClickRemovers.length > 0) {
      const r = _groupClickRemovers.pop();
      // Remove from removers list too
      const idx = removers.indexOf(r);
      if (idx !== -1) removers.splice(idx, 1);
      r();
    }
    rebuildBody();
    if (rovingNav) {
      if (rovingGrid) {
        rovingGrid.refresh();
      } else {
        initRoving();
      }
    }
  }

  function refresh() {
    render(data);
  }

  function getData() {
    return data;
  }

  function destroy() {
    // Remove all delegate listeners
    for (const remove of removers) remove();
    removers.length = 0;
    _groupClickRemovers.length = 0;
    // Tear down RovingGrid
    if (rovingGrid) {
      rovingGrid.destroy();
      rovingGrid = null;
    }
  }

  return { el: table, render, refresh, getData, destroy };
}
