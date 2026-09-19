/* Unit tests for js/col-resize-logic.js — the arithmetic and persistence shape
   behind user-draggable column widths.

   The DOM binding (js/col-resize.js) is covered by tests/js/e2e/col-resize.spec.mjs;
   everything asserted here is pure and must hold with no document at all. */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  DEFAULT_MAX_PX, HANDLE_ATTR, HANDLE_TABLE_ATTR,
  findCol, clampWidth, nextWidth, widthFor, isCustom,
  withWidth, withoutCol, withoutTable,
  serializeWidths, normalizeWidths, colResizeHandleHtml,
} from '../../js/col-resize-logic.js';

/** @type {import('../../js/col-resize-logic.js').ColSpec} */
const PN = { id: 'partid', def: 100, min: 56, max: 400 };
/** @type {import('../../js/col-resize-logic.js').ColSpec} */
const MPN = { id: 'mpn', def: 160, min: 56, max: 400 };
/** @type {import('../../js/col-resize-logic.js').ColSpec} */
const QTY = { id: 'qty', def: 60, min: 36 };

const INV = { id: 'inv', cols: [PN, MPN, QTY] };
const BOM = { id: 'bom', cols: [{ id: 'part', def: 110, min: 44 }] };
const TABLES = [INV, BOM];

describe('clampWidth', () => {
  it('passes a width inside the range through, rounded to whole px', () => {
    expect(clampWidth(180.4, PN)).toBe(180);
    expect(clampWidth(180.6, PN)).toBe(181);
  });

  it('clamps at the column floor rather than letting it collapse', () => {
    expect(clampWidth(10, PN)).toBe(56);
    expect(clampWidth(0, PN)).toBe(56);
    expect(clampWidth(-500, PN)).toBe(56);
  });

  it('clamps at the column ceiling', () => {
    expect(clampWidth(9999, PN)).toBe(400);
  });

  it('falls back to DEFAULT_MAX_PX when the spec declares no ceiling', () => {
    expect(clampWidth(9999, QTY)).toBe(DEFAULT_MAX_PX);
  });

  it('resolves a non-finite candidate to the default instead of throwing', () => {
    expect(clampWidth(NaN, PN)).toBe(100);
    expect(clampWidth(Infinity, PN)).toBe(100);
    // @ts-expect-error deliberately wrong type — a corrupt store can produce it
    expect(clampWidth(undefined, PN)).toBe(100);
  });
});

describe('nextWidth — drag deltas', () => {
  it('grows the column when the pointer moves right', () => {
    expect(nextWidth(100, 60, PN)).toBe(160);
  });

  it('shrinks the column when the pointer moves left', () => {
    expect(nextWidth(200, -60, PN)).toBe(140);
  });

  it('holds at the minimum when dragged far left, without going negative', () => {
    expect(nextWidth(100, -1000, PN)).toBe(PN.min);
  });

  it('holds at the maximum when dragged far right', () => {
    expect(nextWidth(100, 5000, PN)).toBe(400);
  });

  it('anchors on the start width, so dragging out past the clamp and back is exact', () => {
    // Frame-to-frame accumulation would have stuck at the floor here.
    const clamped = nextWidth(200, -1000, PN);
    expect(clamped).toBe(PN.min);
    expect(nextWidth(200, -20, PN)).toBe(180);
  });

  it('treats a non-finite delta as no movement', () => {
    expect(nextWidth(120, NaN, PN)).toBe(120);
  });
});

describe('widthFor / isCustom', () => {
  it('returns the default when nothing is stored', () => {
    expect(widthFor({}, 'inv', PN)).toBe(100);
    expect(isCustom({}, 'inv', 'partid')).toBe(false);
  });

  it('returns the stored override when there is one', () => {
    const state = { inv: { partid: 220 } };
    expect(widthFor(state, 'inv', PN)).toBe(220);
    expect(isCustom(state, 'inv', 'partid')).toBe(true);
  });

  it('re-clamps a stored value that no longer fits the spec', () => {
    // e.g. the token floor was raised in a later build
    expect(widthFor({ inv: { partid: 12 } }, 'inv', PN)).toBe(56);
  });

  it('ignores a stored value of the wrong type', () => {
    // @ts-expect-error corrupt store
    expect(widthFor({ inv: { partid: 'wide' } }, 'inv', PN)).toBe(100);
  });
});

describe('multi-column and multi-table independence', () => {
  it('resizing one column leaves its neighbours at their defaults', () => {
    const state = withWidth({}, 'inv', PN, 240);
    expect(widthFor(state, 'inv', PN)).toBe(240);
    expect(widthFor(state, 'inv', MPN)).toBe(160);
    expect(widthFor(state, 'inv', QTY)).toBe(60);
  });

  it('tracks several columns at once', () => {
    let state = withWidth({}, 'inv', PN, 240);
    state = withWidth(state, 'inv', QTY, 90);
    expect(widthFor(state, 'inv', PN)).toBe(240);
    expect(widthFor(state, 'inv', QTY)).toBe(90);
  });

  it('keeps two tables in separate namespaces (same column id, own widths)', () => {
    let state = withWidth({}, 'inv', { id: 'part', def: 100, min: 40 }, 200);
    state = withWidth(state, 'bom', { id: 'part', def: 110, min: 44 }, 300);
    expect(state.inv.part).toBe(200);
    expect(state.bom.part).toBe(300);
  });

  it('does not mutate the state it is given', () => {
    const before = { inv: { partid: 120 } };
    const after = withWidth(before, 'inv', MPN, 200);
    expect(before).toEqual({ inv: { partid: 120 } });
    expect(after.inv).toEqual({ partid: 120, mpn: 200 });
  });

  it('clamps on the way in, so an out-of-range write cannot be stored', () => {
    expect(withWidth({}, 'inv', PN, 5).inv.partid).toBe(56);
  });
});

describe('reset to defaults', () => {
  it('withoutCol drops one column and leaves the others', () => {
    let state = withWidth({}, 'inv', PN, 240);
    state = withWidth(state, 'inv', MPN, 200);
    const after = withoutCol(state, 'inv', 'partid');
    expect(widthFor(after, 'inv', PN)).toBe(100);
    expect(widthFor(after, 'inv', MPN)).toBe(200);
  });

  it('withoutCol drops the table entry once its last override is gone', () => {
    const state = withWidth({}, 'inv', PN, 240);
    expect(withoutCol(state, 'inv', 'partid')).toEqual({});
  });

  it('withoutTable returns every column of that table to its default', () => {
    let state = withWidth({}, 'inv', PN, 240);
    state = withWidth(state, 'inv', QTY, 90);
    state = withWidth(state, 'bom', { id: 'part', def: 110, min: 44 }, 300);
    const after = withoutTable(state, 'inv');
    expect(after.inv).toBeUndefined();
    expect(widthFor(after, 'inv', PN)).toBe(100);
    expect(widthFor(after, 'inv', QTY)).toBe(60);
    // A reset is per-table: the other table keeps its widths.
    expect(after.bom.part).toBe(300);
  });

  it('resetting an untouched table is a no-op, not an error', () => {
    expect(withoutTable({}, 'inv')).toEqual({});
    expect(withoutCol({}, 'inv', 'partid')).toEqual({});
  });
});

describe('persistence round-trip', () => {
  it('serialize → JSON → normalize preserves every stored width', () => {
    let state = withWidth({}, 'inv', PN, 240);
    state = withWidth(state, 'inv', QTY, 90);
    state = withWidth(state, 'bom', { id: 'part', def: 110, min: 44 }, 300);

    const restored = normalizeWidths(
      JSON.parse(JSON.stringify(serializeWidths(state))), TABLES);
    expect(restored).toEqual({ inv: { partid: 240, qty: 90 }, bom: { part: 300 } });
  });

  it('normalize accepts a raw JSON string as well as an object', () => {
    expect(normalizeWidths('{"inv":{"partid":240}}', TABLES))
      .toEqual({ inv: { partid: 240 } });
  });

  it('serializeWidths returns a detached copy', () => {
    const state = { inv: { partid: 240 } };
    const copy = serializeWidths(state);
    copy.inv.partid = 999;
    expect(state.inv.partid).toBe(240);
  });

  it('serializeWidths strips non-numeric junk', () => {
    // @ts-expect-error corrupt store
    expect(serializeWidths({ inv: { partid: 240, mpn: 'wide' }, junk: 7 }))
      .toEqual({ inv: { partid: 240 } });
  });
});

describe('normalizeWidths — a bad stored value falls back to defaults', () => {
  it('returns empty state for a missing value', () => {
    expect(normalizeWidths(undefined, TABLES)).toEqual({});
    expect(normalizeWidths(null, TABLES)).toEqual({});
  });

  it('returns empty state for unparseable JSON', () => {
    expect(normalizeWidths('{not json', TABLES)).toEqual({});
  });

  it('returns empty state for the wrong shape entirely', () => {
    expect(normalizeWidths(42, TABLES)).toEqual({});
    expect(normalizeWidths([1, 2, 3], TABLES)).toEqual({});
    expect(normalizeWidths({ inv: 'wide' }, TABLES)).toEqual({});
    expect(normalizeWidths({ inv: [100] }, TABLES)).toEqual({});
  });

  it('drops tables and columns this build no longer has', () => {
    expect(normalizeWidths(
      { inv: { partid: 240, gone: 300 }, retired: { x: 1 } }, TABLES))
      .toEqual({ inv: { partid: 240 } });
  });

  it('drops non-numeric and non-finite widths, keeping the good ones', () => {
    expect(normalizeWidths(
      { inv: { partid: 240, mpn: null, qty: NaN } }, TABLES))
      .toEqual({ inv: { partid: 240 } });
  });

  it('clamps stored widths into range rather than trusting the file', () => {
    expect(normalizeWidths({ inv: { partid: -50, mpn: 99999 } }, TABLES))
      .toEqual({ inv: { partid: 56, mpn: 400 } });
  });

  it('never throws, whatever it is handed', () => {
    for (const junk of [undefined, null, 0, '', 'x', [], {}, { inv: null }, true]) {
      expect(() => normalizeWidths(junk, TABLES)).not.toThrow();
    }
    expect(() => normalizeWidths({ inv: { partid: 1 } }, undefined)).not.toThrow();
  });
});

describe('findCol', () => {
  it('finds a column by id', () => {
    expect(findCol(INV.cols, 'mpn')).toBe(MPN);
  });
  it('returns null for an unknown id, so a stale handle is inert', () => {
    expect(findCol(INV.cols, 'nope')).toBeNull();
  });
});

describe('colResizeHandleHtml', () => {
  it('carries both the column id and the table id the binder hit-tests on', () => {
    const html = colResizeHandleHtml('inv', 'partid');
    expect(html).toContain(HANDLE_ATTR + '="partid"');
    expect(html).toContain(HANDLE_TABLE_ATTR + '="inv"');
  });

  it('is hidden from assistive tech and explains itself on hover', () => {
    const html = colResizeHandleHtml('inv', 'partid');
    expect(html).toContain('aria-hidden="true"');
    expect(html).toMatch(/title="[^"]*double-click to reset[^"]*"/);
  });
});

describe('the DOM binding stays on the right side of the zoom seam', () => {
  /* js/col-resize.js writes px it computes from a pointer delta — the exact
     shape of the root-zoom bug. tests/js/zoom-geometry-guard.test.js enforces
     this family-wide; this pins the new module specifically. */
  const src = readFileSync(
    fileURLToPath(new URL('../../js/col-resize.js', import.meta.url)), 'utf8');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

  it('converts pointer readings through toInnerPx() from js/ui-zoom.js', () => {
    expect(code).toMatch(/from '\.\/ui-zoom\.js'/);
    expect(code).toContain('toInnerPx(');
  });

  it('never reads clientX without converting it', () => {
    const rawClientX = code.match(/clientX/g) || [];
    // Two mentions: the drag anchor, and the delta that goes through toInnerPx.
    expect(code).toMatch(/toInnerPx\(\s*ev\.clientX\s*-\s*startClientX\s*\)/);
    expect(rawClientX.length).toBeLessThanOrEqual(2);
  });

  it('reads no rects and no raw window size', () => {
    expect(code).not.toMatch(/getBoundingClientRect/);
    expect(code).not.toMatch(/window\.inner(Width|Height)/);
    expect(code).not.toMatch(/offsetWidth/);
  });
});
