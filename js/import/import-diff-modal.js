// @ts-check
/**
 * js/import/import-diff-modal.js — Review import diff UI.
 *
 * openImportDiffModal(diffEntries, onConfirm, onBack, onCancel)
 *
 * Shows a full-screen overlay with a DataGrid listing each DiffEntry.
 * Per-row include checkboxes (insert/update checked by default; skip
 * unchecked and disabled). Header summary + Confirm/Back/Cancel actions.
 *
 * On Confirm: calls onConfirm(includedRows) with only the included invRows.
 * On Back: calls onBack() — returns to staging, no commit.
 * On Cancel: calls onCancel() — dismisses with no commit.
 *
 * Returns { destroy } so the caller can tear down if needed.
 */

import { DataGrid } from '../components/data-grid.js';

const STATUS_LABEL = { insert: 'Insert', update: 'Update', skip: 'Skip' };

/**
 * @param {import('./import-diff.js').DiffEntry[]} diffEntries
 * @param {(includedRows: Object[]) => void} onConfirm
 * @param {() => void} onBack
 * @param {() => void} onCancel
 * @returns {{ destroy(): void }}
 */
export function openImportDiffModal(diffEntries, onConfirm, onBack, onCancel) {
  // ── Tracked include state ───────────────────────────────────────────────────
  // Per-entry include flag; skips default to false (and are disabled).
  /** @type {Map<number, boolean>} index → included */
  const included = new Map();
  diffEntries.forEach((entry, i) => {
    included.set(i, entry.status !== 'skip');
  });

  // ── Overlay ─────────────────────────────────────────────────────────────────
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.id = 'import-diff-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-labelledby', 'import-diff-title');

  // ── Modal container ──────────────────────────────────────────────────────────
  const modal = document.createElement('div');
  modal.className = 'modal import-diff-modal';

  overlay.appendChild(modal);

  // ── Header ───────────────────────────────────────────────────────────────────
  const header = document.createElement('div');
  header.className = 'import-diff-header';

  const title = document.createElement('h2');
  title.className = 'modal-title';
  title.id = 'import-diff-title';
  title.textContent = 'Review import';
  header.appendChild(title);

  const summary = document.createElement('div');
  summary.className = 'import-diff-summary';
  header.appendChild(summary);

  modal.appendChild(header);

  // ── Table container ──────────────────────────────────────────────────────────
  const tableWrap = document.createElement('div');
  tableWrap.className = 'import-diff-table-wrap';
  modal.appendChild(tableWrap);

  // ── DataGrid ──────────────────────────────────────────────────────────────────
  /**
   * Build the checkbox cell content for a row.
   * @param {import('./import-diff.js').DiffEntry} entry
   * @param {number} idx
   * @returns {Node}
   */
  function buildCheckbox(entry, idx) {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'import-diff-cb';
    cb.checked = included.get(idx) || false;
    cb.disabled = entry.status === 'skip';
    cb.setAttribute('aria-label', 'Include row ' + (idx + 1));
    cb.addEventListener('change', () => {
      included.set(idx, cb.checked);
      updateSummary();
    });
    return cb;
  }

  /**
   * Build status badge element.
   * @param {import('./import-diff.js').DiffEntry} entry
   * @returns {Node}
   */
  function buildBadge(entry) {
    const span = document.createElement('span');
    span.className = 'import-diff-badge import-diff-badge--' + entry.status;
    span.textContent = STATUS_LABEL[entry.status] || entry.status;
    return span;
  }

  /**
   * Build the qty display: for updates show "current → resulting"; for inserts "+N".
   * @param {import('./import-diff.js').DiffEntry} entry
   * @returns {string}
   */
  function qtyLabel(entry) {
    if (entry.status === 'skip') return entry.skipReason || '—';
    if (entry.status === 'update') {
      return entry.currentQty + ' → ' + entry.resultingQty + ' (+' + entry.addQty + ')';
    }
    return '+' + entry.addQty;
  }

  const grid = DataGrid(tableWrap, {
    columns: [
      {
        key: '_include',
        label: '',
        width: '36px',
        align: 'center',
        render: (entry, idx) => buildCheckbox(entry, idx),
      },
      {
        key: '_status',
        label: 'Status',
        width: '80px',
        render: (entry) => buildBadge(entry),
      },
      {
        key: '_part',
        label: 'Part #',
        width: '110px',
        mono: true,
        render: (entry) => {
          const s = document.createElement('span');
          s.textContent = entry.partKey ||
            (entry.row['LCSC Part Number'] || entry.row['Manufacture Part Number'] || '—');
          return s;
        },
      },
      {
        key: '_mpn',
        label: 'MPN',
        width: '130px',
        render: (entry) => {
          const s = document.createElement('span');
          s.textContent = entry.row['Manufacture Part Number'] || '—';
          return s;
        },
      },
      {
        key: '_qty',
        label: 'Qty',
        width: '160px',
        mono: true,
        align: 'right',
        render: (entry) => {
          const s = document.createElement('span');
          s.textContent = qtyLabel(entry);
          return s;
        },
      },
      {
        key: '_price',
        label: 'Unit cost',
        width: '90px',
        mono: true,
        align: 'right',
        render: (entry) => {
          const s = document.createElement('span');
          const price = entry.row['Unit Price($)'] || entry.row['Unit Price'] || '';
          s.textContent = price ? '$' + price : '—';
          return s;
        },
      },
    ],
    rowKey: (_entry, idx) => String(idx),
    getRowClass: (entry) => {
      if (entry.status === 'insert') return 'import-diff-row--insert';
      if (entry.status === 'update') return 'import-diff-row--update';
      return 'import-diff-row--skip';
    },
    emptyMessage: 'No rows to review',
    rovingNav: false,
  });

  grid.render(diffEntries);

  // ── Footer actions ───────────────────────────────────────────────────────────
  const actions = document.createElement('div');
  actions.className = 'modal-actions import-diff-actions';

  const backBtn = document.createElement('button');
  backBtn.type = 'button';
  backBtn.className = 'btn-md';
  backBtn.textContent = 'Back';
  backBtn.addEventListener('click', () => { destroy(); onBack(); });

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'btn-md';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.addEventListener('click', () => { destroy(); onCancel(); });

  const confirmBtn = document.createElement('button');
  confirmBtn.type = 'button';
  confirmBtn.id = 'import-diff-confirm-btn';
  confirmBtn.className = 'btn-md btn-blue';
  confirmBtn.textContent = 'Confirm import';
  confirmBtn.addEventListener('click', () => {
    const includedRows = diffEntries
      .filter((entry, i) => included.get(i) && entry.status !== 'skip')
      .map(entry => entry.row);
    destroy();
    onConfirm(includedRows);
  });

  actions.appendChild(backBtn);
  actions.appendChild(cancelBtn);
  actions.appendChild(confirmBtn);
  modal.appendChild(actions);

  // ── Summary line ─────────────────────────────────────────────────────────────
  function updateSummary() {
    const counts = { insert: 0, update: 0, skip: 0 };
    diffEntries.forEach((entry, i) => {
      if (entry.status === 'skip') {
        counts.skip++;
      } else if (included.get(i)) {
        counts[entry.status]++;
      }
    });

    const parts = [];
    if (counts.insert > 0) parts.push(counts.insert + ' to insert');
    if (counts.update > 0) parts.push(counts.update + ' to update');
    if (counts.skip > 0) parts.push(counts.skip + ' skipped');
    summary.textContent = parts.length > 0 ? parts.join(' · ') : 'Nothing to import';

    const anyIncluded = diffEntries.some((entry, i) =>
      entry.status !== 'skip' && included.get(i)
    );
    confirmBtn.disabled = !anyIncluded;
  }

  updateSummary();

  // ── Mount & focus ─────────────────────────────────────────────────────────────
  document.body.appendChild(overlay);
  confirmBtn.focus();

  // Escape key = Back (safe; no commit)
  function onKeydown(e) {
    if (e.key === 'Escape') { destroy(); onBack(); }
  }
  document.addEventListener('keydown', onKeydown);

  function destroy() {
    document.removeEventListener('keydown', onKeydown);
    grid.destroy();
    overlay.remove();
  }

  return { destroy };
}
