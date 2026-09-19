/* Merged-view provenance: what a row's badge claims, which fields two servers
   disagreed on, where a write is allowed to land, and when the totals on screen
   are incomplete.

   The through-line of this file is that every function has to behave sensibly
   for a row that carries NO provenance at all — the single-server view, which
   is what the app shows almost all of the time and which must not change
   behaviour because a merged view exists. */

import { describe, it, expect } from 'vitest';

import {
  sourceEntries,
  isMergedRow,
  sourceNames,
  sourceBadge,
  conflictFields,
  hasConflict,
  conflictNote,
  writeTarget,
  writeTargetForAll,
  partialViewNote,
  serverOptions,
} from '../../js/inventory/inv-source-logic.js';
import { matchesPredicate } from '../../js/components/predicate-ui.js';
import {
  buildInventoryFields,
  extractInventoryField,
  filterByPredicate,
} from '../../js/inventory/filter-chips-fields.js';
import { filterByQuery } from '../../js/inventory/inventory-logic.js';

/** A row as `GET /v1/parts` serves it in a merged view. */
function merged(overrides = {}) {
  return {
    lcsc: 'C1000', mpn: 'MPN-1', description: 'cap', qty: 1250,
    sources: [
      { id: 'bench', name: 'bench', qty: 850 },
      { id: 'shop', name: 'shop', qty: 400 },
    ],
    conflicts: [],
    ...overrides,
  };
}

/** A row as a single server serves it: no `sources`, no `conflicts`. */
const PLAIN = { lcsc: 'C2000', mpn: 'MPN-2', description: 'res', qty: 12 };

const ONE_SOURCE = merged({
  qty: 850,
  sources: [{ id: 'bench', name: 'bench', qty: 850 }],
});

describe('sourceEntries', () => {
  it('is empty for a row from a single-server view', () => {
    expect(sourceEntries(PLAIN)).toEqual([]);
    expect(isMergedRow(PLAIN)).toBe(false);
    expect(sourceNames(PLAIN)).toEqual([]);
  });

  it('keeps source order and coerces a missing qty to 0', () => {
    const row = merged({ sources: [{ id: 'shop', name: 'shop' }, { id: 'bench', name: 'bench', qty: 5 }] });
    expect(sourceEntries(row)).toEqual([
      { id: 'shop', name: 'shop', qty: 0 },
      { id: 'bench', name: 'bench', qty: 5 },
    ]);
  });

  it('drops an entry with no id, because nothing could be written to it', () => {
    const row = merged({ sources: [{ name: 'ghost', qty: 9 }, { id: 'bench', name: 'bench', qty: 1 }] });
    expect(sourceEntries(row).map((s) => s.id)).toEqual(['bench']);
  });

  it('falls back to the id when a source has no display name', () => {
    expect(sourceEntries(merged({ sources: [{ id: 'bench', qty: 3 }] }))[0].name).toBe('bench');
  });

  it('survives junk instead of an array', () => {
    expect(sourceEntries({ sources: 'bench' })).toEqual([]);
    expect(sourceEntries(null)).toEqual([]);
    expect(sourceEntries(undefined)).toEqual([]);
  });
});

describe('sourceBadge', () => {
  it('shows nothing at all in a single-server view', () => {
    expect(sourceBadge(PLAIN).show).toBe(false);
  });

  it('NAMES the server when there is only one', () => {
    const badge = sourceBadge(ONE_SOURCE);
    expect(badge.show).toBe(true);
    expect(badge.multi).toBe(false);
    expect(badge.label).toBe('bench');
  });

  it('COUNTS the servers when there are several, and offers the breakdown', () => {
    const badge = sourceBadge(merged());
    expect(badge.multi).toBe(true);
    expect(badge.label).toBe('2 servers');
    expect(badge.title).toContain('bench (850)');
    expect(badge.title).toContain('shop (400)');
  });
});

describe('conflicts', () => {
  it('is quiet when the sources agreed', () => {
    expect(conflictFields(merged())).toEqual([]);
    expect(conflictNote(merged())).toBe('');
    expect(hasConflict(merged(), 'description')).toBe(false);
  });

  it('names the disagreeing fields and whose value is on screen', () => {
    const row = merged({ conflicts: ['description', 'package'] });
    expect(conflictFields(row)).toEqual(['description', 'package']);
    expect(hasConflict(row, 'description')).toBe(true);
    expect(hasConflict(row, 'mpn')).toBe(false);
    // The merge resolves by first-non-empty in source order, so the winner is
    // the first contributing source — saying so is the whole point of the mark.
    expect(conflictNote(row)).toContain('bench');
    expect(conflictNote(row)).toContain('description, package');
  });

  it('ignores junk in the conflicts list rather than rendering it', () => {
    expect(conflictFields({ conflicts: ['', '  ', 'mpn', 7] })).toEqual(['mpn']);
    expect(conflictFields({ conflicts: 'mpn' })).toEqual([]);
  });
});

describe('writeTarget', () => {
  it('asks for no routing at all in a single-server view', () => {
    const t = writeTarget(PLAIN);
    expect(t).toMatchObject({ ok: true, sourceId: '', ambiguous: false });
  });

  it('routes to the one server that holds the stock', () => {
    expect(writeTarget(ONE_SOURCE)).toMatchObject({ ok: true, sourceId: 'bench' });
  });

  it('REFUSES to guess when the stock is split', () => {
    const t = writeTarget(merged());
    expect(t.ok).toBe(false);
    expect(t.ambiguous).toBe(true);
    expect(t.options.map((s) => s.id)).toEqual(['bench', 'shop']);
    expect(t.reason).toContain('bench (850)');
  });

  it('accepts an explicit choice', () => {
    expect(writeTarget(merged(), 'shop')).toMatchObject({ ok: true, sourceId: 'shop' });
  });

  it('rejects a choice the row no longer offers, rather than sending it anyway', () => {
    // A stale pick — the server was chosen before a refresh dropped it from the
    // row. Sending it would write to a machine that does not hold the part.
    const t = writeTarget(merged(), 'attic');
    expect(t.ok).toBe(false);
    expect(t.reason).toContain('no longer holds this part');
  });
});

describe('writeTargetForAll (a BOM consume: one request, one server)', () => {
  it('needs no routing when nothing is merged', () => {
    expect(writeTargetForAll([PLAIN, { ...PLAIN, lcsc: 'C3' }]))
      .toMatchObject({ ok: true, sourceId: '' });
  });

  it('routes when every part is on the same single server', () => {
    expect(writeTargetForAll([ONE_SOURCE, { ...ONE_SOURCE, lcsc: 'C3' }]))
      .toMatchObject({ ok: true, sourceId: 'bench' });
  });

  it('refuses when the parts span two servers', () => {
    const shopOnly = merged({ sources: [{ id: 'shop', name: 'shop', qty: 4 }] });
    const t = writeTargetForAll([ONE_SOURCE, shopOnly]);
    expect(t.ok).toBe(false);
    expect(t.options.map((s) => s.id)).toEqual(['bench', 'shop']);
  });

  it('refuses when ONE part is itself split, even if that is the only server pair', () => {
    // Splitting this into two consumes would mean two writes that can fail
    // independently — a half-consumed BOM with nothing to roll it back.
    const t = writeTargetForAll([merged()]);
    expect(t.ok).toBe(false);
    expect(t.reason).toContain('more than one server');
  });
});

describe('partialViewNote', () => {
  it('says nothing when every source answered', () => {
    const note = partialViewNote([
      { id: 'bench', name: 'bench', ok: true, error: '', status: 200 },
      { id: 'shop', name: 'shop', ok: true, error: '', status: 200 },
    ]);
    expect(note.degraded).toBe(false);
    expect(note.message).toBe('');
  });

  it('says nothing for a single-server view, which reports no statuses at all', () => {
    expect(partialViewNote([]).degraded).toBe(false);
    expect(partialViewNote(undefined).degraded).toBe(false);
  });

  it('says the TOTALS are incomplete, not merely that a server is down', () => {
    const note = partialViewNote([
      { id: 'bench', name: 'bench', ok: true, error: '', status: 200 },
      { id: 'shop', name: 'shop', ok: false, error: 'ConnectError: refused', status: null },
    ]);
    expect(note.degraded).toBe(true);
    expect(note.failed.map((f) => f.id)).toEqual(['shop']);
    expect(note.message).toContain('shop');
    expect(note.message).toContain('incomplete');
  });

  it('names every failed source', () => {
    const note = partialViewNote([
      { id: 'a', name: 'attic', ok: false, error: 'timeout' },
      { id: 'b', name: 'barn', ok: false, error: 'timeout' },
    ]);
    expect(note.failed).toHaveLength(2);
    expect(note.message).toContain('attic, barn');
  });
});

describe('the server filter chip', () => {
  const inventory = [
    merged(),
    merged({ lcsc: 'C3', qty: 4, sources: [{ id: 'shop', name: 'shop', qty: 4 }] }),
  ];

  it('offers no Server field at all in a single-server view', () => {
    const keys = buildInventoryFields([PLAIN]).map((f) => f.key);
    expect(keys).not.toContain('server');
    expect(serverOptions([PLAIN])).toEqual([]);
  });

  it('derives its options from the rows, like the Section chip does', () => {
    const field = buildInventoryFields(inventory).find((f) => f.key === 'server');
    expect(field).toMatchObject({ key: 'server', label: 'Server', type: 'enum' });
    expect(field.options).toEqual(['bench', 'shop']);
  });

  it('extracts the FULL set of servers, not one of them', () => {
    expect(extractInventoryField(merged(), 'server')).toEqual(['bench', 'shop']);
  });

  it('matches a split row from either of its servers', () => {
    // The lie this guards against: reporting one server for a split row would
    // make `server is shop` hide a part that is, in fact, in the shop.
    expect(filterByPredicate(inventory, {
      op: 'and', rules: [{ field: 'server', operator: 'is', value: 'shop' }],
    })).toHaveLength(2);
    expect(filterByPredicate(inventory, {
      op: 'and', rules: [{ field: 'server', operator: 'is', value: 'bench' }],
    })).toHaveLength(1);
  });

  it('negates as "no member matches", so is/is_not cannot both be true', () => {
    const split = merged();
    expect(matchesPredicate({ server: ['bench', 'shop'] },
      { field: 'server', operator: 'is', value: 'bench' })).toBe(true);
    expect(matchesPredicate({ server: ['bench', 'shop'] },
      { field: 'server', operator: 'is_not', value: 'bench' })).toBe(false);
    expect(filterByPredicate([split], {
      op: 'and', rules: [{ field: 'server', operator: 'is_not', value: 'bench' }],
    })).toHaveLength(0);
  });

  it('supports the enum `in` operator over the set', () => {
    expect(matchesPredicate({ server: ['shop'] },
      { field: 'server', operator: 'in', value: ['bench', 'shop'] })).toBe(true);
    expect(matchesPredicate({ server: ['attic'] },
      { field: 'server', operator: 'in', value: ['bench', 'shop'] })).toBe(false);
  });

  it('leaves scalar fields exactly as they were', () => {
    expect(matchesPredicate({ mpn: 'ABC' }, { field: 'mpn', operator: 'contains', value: 'b' })).toBe(true);
    expect(matchesPredicate({ qty: 5 }, { field: 'qty', operator: 'gt', value: 3 })).toBe(true);
  });
});

describe('search', () => {
  it('finds a part by the name of the server holding it', () => {
    const rows = [merged({ lcsc: 'C1' }), { ...PLAIN, lcsc: 'C2' }];
    expect(filterByQuery(rows, 'bench').map((r) => r.lcsc)).toEqual(['C1']);
  });

  it('does not change what a single-server search matches', () => {
    expect(filterByQuery([PLAIN], 'res')).toHaveLength(1);
    expect(filterByQuery([PLAIN], 'bench')).toHaveLength(0);
  });
});
