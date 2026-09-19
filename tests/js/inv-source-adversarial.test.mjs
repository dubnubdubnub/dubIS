// @ts-check
/* inv-source-adversarial.test.mjs — what a merged row is allowed to claim when
 * the fetch behind it did not fully succeed.
 *
 * `tests/js/inv-source-logic.test.mjs` covers each function against a
 * well-formed merged row. This file covers the case the feature actually has to
 * survive: the row is well-formed and the *view* is not. A merged read degrades
 * rather than fails (`server/fanout.py`), so every row on screen during a
 * partial view is a perfectly plausible row that is missing a machine's worth of
 * stock — and the row itself carries no trace of that.
 *
 * The invariant being attacked throughout:
 *
 *   **A number the user can act on must not look more complete than it is.**
 *
 * That has two halves, and the second is the one nobody writes a test for:
 * the banner (does the view admit it is partial?) and the *write* (does a
 * decision made from a partial row get taken at face value?).
 */

import { describe, it, expect } from 'vitest';
import {
  sourceEntries,
  sourceBadge,
  writeTarget,
  writeTargetForAll,
  partialViewNote,
  serverOptions,
  conflictNote,
} from '../../js/inventory/inv-source-logic.js';

/** A merged row as `domain/federation.py` emits it. */
function row(key, sources, extra = {}) {
  return {
    lcsc: key,
    qty: sources.reduce((n, s) => n + s.qty, 0),
    sources,
    conflicts: [],
    ...extra,
  };
}

const BENCH = { id: 'bench', name: 'bench', qty: 850 };
const SHOP = { id: 'shop', name: 'shop', qty: 400 };

describe('partialViewNote: every way a source fails to contribute', () => {
  it('reports a source that failed, whatever the error looks like', () => {
    for (const error of ['ConnectError: refused', 'HTTP 500', '', null, undefined]) {
      const note = partialViewNote([
        { id: 'local', name: 'Local', ok: true, error: '', status: 200 },
        { id: 'shop', name: 'shop', ok: false, error, status: null },
      ]);
      expect(note.degraded, `error=${String(error)}`).toBe(true);
      expect(note.message).toContain('shop');
      expect(note.message).toContain('incomplete');
    }
  });

  it('treats a status entry with no `ok` at all as a failure, not a success', () => {
    /* A hub one version behind, or a truncated body: `ok` absent must fail
       safe. The alternative — absent means fine — is the same mistake as a
       missing probe reading as a green dot. */
    const note = partialViewNote([{ id: 'shop', name: 'shop' }]);
    expect(note.degraded).toBe(true);
  });

  it('never reports a source that is simply not in the list', () => {
    /* The counterpart defect lives on the server: a source excluded from the
       fan-out (disabled) never gets a status entry, so there is nothing here to
       report. This test states the boundary — this module can only speak about
       sources it was told about, which is why the server has to tell it about
       every one. */
    const note = partialViewNote([{ id: 'local', name: 'Local', ok: true }]);
    expect(note.degraded).toBe(false);
    expect(note.failed).toEqual([]);
  });

  it('names every failed source, not just the first', () => {
    const note = partialViewNote([
      { id: 'a', name: 'attic', ok: false, error: 'x' },
      { id: 'b', name: 'bench', ok: true },
      { id: 'c', name: 'cellar', ok: false, error: 'y' },
    ]);
    expect(note.failed.map((f) => f.name)).toEqual(['attic', 'cellar']);
    expect(note.message).toContain('attic');
    expect(note.message).toContain('cellar');
  });
});

describe('writeTarget: a decision taken from an incomplete row', () => {
  it('routes an unambiguous row to the one server holding it', () => {
    expect(writeTarget(row('C1000', [BENCH]))).toMatchObject({ ok: true, sourceId: 'bench' });
  });

  it('refuses to guess when the stock is split', () => {
    const target = writeTarget(row('C1000', [BENCH, SHOP]));
    expect(target.ok).toBe(false);
    expect(target.options.map((o) => o.id)).toEqual(['bench', 'shop']);
  });

  it('rejects a stale choice rather than sending it anyway', () => {
    expect(writeTarget(row('C1000', [BENCH, SHOP]), 'attic').ok).toBe(false);
  });

  it('DEFECT: a row whose second server is asleep reads as unambiguous', () => {
    /* DEFECT (medium-high, design gap).
     *
     * `C1000` is 850 on the bench and 400 in the shop. The shop is asleep, so
     * the merged fetch degraded and the row came back naming bench alone. The
     * row is now byte-for-byte identical to a row that genuinely only ever
     * lived on the bench:
     *
     *     writeTarget(rowWhileShopIsAsleep)  ===  writeTarget(benchOnlyRow)
     *
     * so the Adjust modal shows a flat "Server: bench" instead of the chooser,
     * and "set qty to 900" — a number the user worked out from a total that was
     * missing 400 parts — is applied to bench without a question being asked.
     * The banner above the grid says the totals are incomplete; nothing carries
     * that fact into the one place where it changes what happens.
     *
     * Proposed contract: `writeTarget` takes the view's degraded state and
     * refuses to call a single-source row unambiguous while a source is
     * missing — the same refusal it already makes for a genuinely split row,
     * for the same reason. The extra argument is additive; today it is ignored,
     * which is why this test fails.
     */
    const degraded = { degraded: true, missing: ['shop'] };
    const target = writeTarget(row('C1000', [BENCH]), '', degraded);
    expect(target.ok,
      'a write must not be taken at face value from a view that admits it is '
      + 'missing a server').toBe(false);
  });

  it('DEFECT: a whole-BOM consume from a partial view is equally unguarded', () => {
    /* DEFECT (medium-high, design gap) — the same hole with a bigger blast
     * radius. `writeTargetForAll` refuses when the parts span two servers
     * precisely so no BOM is half-consumed. A partial view hides the span: with
     * the shop asleep every row names bench alone, the consume routes to bench,
     * and the parts that were really in the shop come out of the bench's stock
     * instead. Nothing fails, and the bench is now wrong about several parts at
     * once. */
    const items = [row('C1', [BENCH]), row('C2', [BENCH])];
    const target = writeTargetForAll(items, { degraded: true, missing: ['shop'] });
    expect(target.ok,
      'a consume must stop while the view is admittedly missing a server').toBe(false);
  });
});

describe('sourceBadge: what a row says about itself', () => {
  it('names the one server rather than counting to one', () => {
    expect(sourceBadge(row('C1', [BENCH]))).toMatchObject({ show: true, multi: false, label: 'bench' });
  });

  it('counts and offers the breakdown when there are several', () => {
    const badge = sourceBadge(row('C1', [BENCH, SHOP]));
    expect(badge).toMatchObject({ show: true, multi: true, count: 2 });
    expect(badge.title).toContain('1250');
  });

  it('shows nothing in a single-server view, where provenance is the tab', () => {
    expect(sourceBadge({ lcsc: 'C1', qty: 5 }).show).toBe(false);
  });

  it('survives a breakdown that disagrees with the row it is on', () => {
    /* The merge guarantees the breakdown sums to `qty`, and this module says it
       relies on that and does not re-check. Make the guarantee false anyway — a
       peer running a different build, a hand-built fixture — and confirm the
       badge degrades to a wrong-but-harmless number rather than throwing and
       taking the whole render down with it. */
    const broken = { lcsc: 'C1', qty: 9999, sources: [BENCH, SHOP], conflicts: [] };
    expect(() => sourceBadge(broken)).not.toThrow();
    expect(sourceBadge(broken).count).toBe(2);
  });

  it('drops an entry with no id, because nothing could ever be written to it', () => {
    const entries = sourceEntries({ sources: [{ name: 'nameless', qty: 5 }, BENCH] });
    expect(entries.map((e) => e.id)).toEqual(['bench']);
  });

  it('coerces a non-numeric qty to 0 rather than rendering NaN', () => {
    const entries = sourceEntries({ sources: [{ id: 'x', name: 'x', qty: 'lots' }] });
    expect(entries[0].qty).toBe(0);
  });
});

describe('adversarial source metadata', () => {
  it('handles a very long and a unicode server name without truncating the id', () => {
    const long = 'ω'.repeat(400);
    const entries = sourceEntries({ sources: [{ id: 'bench', name: long, qty: 1 }] });
    expect(entries[0].id).toBe('bench');
    expect(entries[0].name).toBe(long);
  });

  it('falls back to the id when a name is whitespace, so a badge is never blank', () => {
    const entries = sourceEntries({ sources: [{ id: 'bench', name: '   ', qty: 1 }] });
    expect(entries[0].name).toBe('bench');
  });

  it('lists every distinct server name across the inventory, sorted', () => {
    const inventory = [row('C1', [BENCH]), row('C2', [SHOP, BENCH]), { lcsc: 'C3', qty: 1 }];
    expect(serverOptions(inventory)).toEqual(['bench', 'shop']);
  });

  it('says whose value is on screen when servers disagree', () => {
    const conflicted = row('C1', [BENCH, SHOP], { conflicts: ['description', 'package'] });
    const note = conflictNote(conflicted);
    expect(note).toContain('description');
    expect(note).toContain('package');
    expect(note, 'the merge resolves first-non-empty in source order, so the '
      + 'row is showing the FIRST source\'s value').toContain('bench');
  });
});
