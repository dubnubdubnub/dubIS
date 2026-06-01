import { describe, it, expect } from 'vitest';
import {
  createState, selectToken, selectCell, applyPending,
  combineTokens, setCellValue, tokenText,
  selectTokens, setPage, clearPending,
} from '../../js/import/mfg-direct/ocr-overlay/ocr-overlay-state.js';

const payload = {
  template: 'lcsc',
  pages: [{
    image_b64: 'AAAA', width: 100, height: 50,
    words: [
      { text: 'C12624', x: 0, y: 0, w: 30, h: 8, conf: 95, line_id: 0 },
      { text: 'KT-0603G', x: 0, y: 10, w: 40, h: 8, conf: 90, line_id: 1 },
    ],
    lines: [{ text: 'C12624', x: 0, y: 0, w: 30, h: 8, conf: 95 }],
  }],
  prefill_rows: [{ distributor_pn: '', mpn: '', quantity: 0, unit_price: 0 }],
};

describe('ocr-overlay-state', () => {
  it('word-first then cell fills the cell', () => {
    let s = createState(payload);
    s = selectToken(s, '0:w:0');
    expect(s.pending.kind).toBe('source');
    s = selectCell(s, { row: 0, field: 'distributor_pn' });
    s = applyPending(s);
    expect(s.rows[0].distributor_pn).toBe('C12624');
    expect(s.pending.kind).toBe(null);
  });

  it('cell-first then word fills the cell (reverse direction)', () => {
    let s = createState(payload);
    s = selectCell(s, { row: 0, field: 'mpn' });
    expect(s.pending.kind).toBe('target');
    s = selectToken(s, '0:w:1');
    s = applyPending(s);
    expect(s.rows[0].mpn).toBe('KT-0603G');
  });

  it('combines multiple tokens in x/y reading order', () => {
    expect(combineTokens(payload.pages[0], ['0:w:1', '0:w:0']))
      .toBe('C12624 KT-0603G');
  });

  it('double-click edit sets a value directly', () => {
    let s = createState(payload);
    s = setCellValue(s, 0, 'quantity', '4000');
    expect(s.rows[0].quantity).toBe('4000');
  });

  it('tokenText returns the underlying token text and "" for bad ids', () => {
    expect(tokenText(payload.pages, '0:w:0')).toBe('C12624');
    expect(tokenText(payload.pages, '0:l:0')).toBe('C12624');
    expect(tokenText(payload.pages, '0:w:9')).toBe('');
  });

  it('selectTokens (drag-combine) keeps a pending target cell and combines', () => {
    let s = createState(payload);
    s = selectCell(s, { row: 0, field: 'mpn' });
    expect(s.pending.kind).toBe('target');
    s = selectTokens(s, ['0:w:1', '0:w:0']);
    expect(s.pending.kind).toBe('source');
    expect(s.pending.cell).toEqual({ row: 0, field: 'mpn' });
    s = applyPending(s);
    expect(s.rows[0].mpn).toBe('C12624 KT-0603G');
  });

  it('setPage switches page and clears pending', () => {
    let s = createState(payload);
    s = selectToken(s, '0:w:0');
    expect(s.pending.kind).toBe('source');
    s = setPage(s, 0);
    expect(s.pageIdx).toBe(0);
    expect(s.pending.kind).toBe(null);
    expect(s.pending.tokenIds).toEqual([]);
  });

  it('applyPending is a no-op when the assignment is incomplete', () => {
    let s = createState(payload);
    s = selectToken(s, '0:w:0');
    const after = applyPending(s);
    expect(after).toBe(s);
    expect(after.rows[0].distributor_pn).toBe('');
  });

  it('clearPending resets the pending selection', () => {
    let s = createState(payload);
    s = selectCell(s, { row: 0, field: 'mpn' });
    s = clearPending(s);
    expect(s.pending.kind).toBe(null);
    expect(s.pending.cell).toBe(null);
  });
});
