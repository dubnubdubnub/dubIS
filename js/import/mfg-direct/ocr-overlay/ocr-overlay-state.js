/* ocr-overlay-state.js — pure state for the OCR overlay modal (no DOM, no api). */

export function createState(payload) {
  return {
    template: payload.template,
    pages: payload.pages || [],
    pageIdx: 0,
    rows: (payload.prefill_rows || []).map(r => ({ ...r })),
    lowConf: (payload.prefill_rows || []).map(r => new Set(r._low_conf || [])),
    pending: { kind: null, tokenIds: [], cell: null },
    tokenMode: 'w',
    zoom: 1,
    fullscreen: false,
  };
}

/** Clamp and store the scan-image zoom factor (1× .. 4×). Non-numbers → 1. */
export function setZoom(state, zoom) {
  const z = Number(zoom);
  const clamped = Number.isFinite(z) ? Math.max(1, Math.min(4, z)) : 1;
  return { ...state, zoom: clamped };
}

function tokenFromId(pages, id) {
  const [p, kind, idx] = id.split(':');
  const page = pages[+p];
  const arr = kind === 'w' ? page.words : page.lines;
  return arr[+idx];
}

export function tokenText(pages, id) {
  const t = tokenFromId(pages, id);
  return t ? t.text : '';
}

/** Combine token ids into one string in reading order (top, then left). */
export function combineTokens(page, ids) {
  const toks = ids.map(id => {
    const [, kind, idx] = id.split(':');
    return (kind === 'w' ? page.words : page.lines)[+idx];
  });
  toks.sort((a, b) => (a.y - b.y) || (a.x - b.x));
  return toks.map(t => t.text).join(' ');
}

export function selectToken(state, tokenId) {
  if (state.pending.kind === 'target' && state.pending.cell) {
    return { ...state, pending: { ...state.pending, kind: 'source', tokenIds: [tokenId] } };
  }
  return { ...state, pending: { kind: 'source', tokenIds: [tokenId], cell: null } };
}

export function selectTokens(state, tokenIds) {
  const keepCell = state.pending.cell || null;
  return { ...state, pending: { kind: 'source', tokenIds, cell: keepCell } };
}

export function selectCell(state, cell) {
  if (state.pending.kind === 'source' && state.pending.tokenIds.length) {
    return { ...state, pending: { ...state.pending, cell } };
  }
  return { ...state, pending: { kind: 'target', tokenIds: [], cell } };
}

/** Complete an assignment if both a token set and a target cell are pending. */
export function applyPending(state) {
  const { tokenIds, cell } = state.pending;
  if (!tokenIds.length || !cell) return state;
  const page = state.pages[state.pageIdx];
  const value = combineTokens(page, tokenIds);
  const rows = state.rows.map((r, i) =>
    i === cell.row ? { ...r, [cell.field]: value } : r);
  const lowConf = state.lowConf.map((s, i) => {
    if (i !== cell.row) return s;
    const next = new Set(s); next.delete(cell.field); return next;
  });
  return { ...state, rows, lowConf, pending: { kind: null, tokenIds: [], cell: null } };
}

export function setCellValue(state, row, field, value) {
  const rows = state.rows.map((r, i) => i === row ? { ...r, [field]: value } : r);
  return { ...state, rows };
}

export function setPage(state, pageIdx) {
  return { ...state, pageIdx, pending: { kind: null, tokenIds: [], cell: null } };
}

/** Switch the token rendering mode ('w' = words, 'l' = lines). Clears pending. */
export function setTokenMode(state, mode) {
  const tokenMode = mode === 'l' ? 'l' : 'w';
  return { ...state, tokenMode, pending: { kind: null, tokenIds: [], cell: null } };
}

export function clearPending(state) {
  return { ...state, pending: { kind: null, tokenIds: [], cell: null } };
}

/** Append a blank row (all grid fields empty). */
export function addRow(state) {
  return {
    ...state,
    rows: [...state.rows, {}],
    lowConf: [...state.lowConf, new Set()],
  };
}

/** Remove row `ri`; drop its lowConf and clear a pending cell at/after it. */
export function deleteRow(state, ri) {
  if (ri < 0 || ri >= state.rows.length) return state;
  const rows = state.rows.slice(); rows.splice(ri, 1);
  const lowConf = state.lowConf.slice(); lowConf.splice(ri, 1);
  let pending = state.pending;
  if (pending.cell && pending.cell.row >= ri) {
    pending = { kind: null, tokenIds: [], cell: null };
  }
  return { ...state, rows, lowConf, pending };
}

/**
 * Shift one column's values up or down by one with blank-fill (no data loss).
 * down: insert blank at top, push down; grow a row if the bottom value would
 * fall off. up: drop the top value, pull up, blank the bottom. Other columns
 * are untouched. lowConf flags for that column move with their values.
 */
export function shiftColumn(state, field, dir) {
  const n = state.rows.length;
  if (n === 0) return state;
  const rows = state.rows.map(r => ({ ...r }));
  const lowConf = state.lowConf.map(s => new Set(s));
  const moveFlag = (toIdx, fromIdx) => {
    if (lowConf[fromIdx] && lowConf[fromIdx].has(field)) lowConf[toIdx].add(field);
    else lowConf[toIdx].delete(field);
  };
  if (dir === 'down') {
    const bottom = String(rows[n - 1][field] ?? '').trim();
    if (bottom !== '') { rows.push({}); lowConf.push(new Set()); }
    for (let i = rows.length - 1; i > 0; i--) {
      rows[i][field] = rows[i - 1][field] ?? '';
      moveFlag(i, i - 1);
    }
    rows[0][field] = '';
    lowConf[0].delete(field);
  } else {
    for (let i = 0; i < n - 1; i++) {
      rows[i][field] = rows[i + 1][field] ?? '';
      moveFlag(i, i + 1);
    }
    rows[n - 1][field] = '';
    lowConf[n - 1].delete(field);
  }
  return { ...state, rows, lowConf };
}
