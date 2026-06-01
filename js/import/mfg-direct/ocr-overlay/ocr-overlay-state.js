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
  };
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
