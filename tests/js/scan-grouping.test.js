// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { buildGroupPayloads } from '../../js/import/mfg-direct/scan-grouping.js';

const PHOTOS = [
  { index: 0, filename: 'a.png', image_b64: 'AAA',
    pages: [{ image_b64: 'p0' }], prefill_rows: [{ mpn: 'X' }] },
  { index: 1, filename: 'b.png', image_b64: 'BBB',
    pages: [{ image_b64: 'p1' }], prefill_rows: [{ mpn: 'Y' }, { mpn: 'Z' }] },
];

describe('buildGroupPayloads', () => {
  it('one group per photo → separate payloads, each with its own pages/rows/source', () => {
    const out = buildGroupPayloads(PHOTOS, [[0], [1]], 'lcsc');
    expect(out).toHaveLength(2);

    expect(out[0].prefill_rows).toEqual([{ mpn: 'X' }]);
    expect(out[0].pages).toEqual([{ image_b64: 'p0' }]);
    expect(out[0].image_b64).toBe('AAA');   // first photo's original bytes = PO source
    expect(out[0].filename).toBe('a.png');
    expect(out[0].template).toBe('lcsc');
    expect(out[0].poLabel).toBe('PO 1 of 2');
    expect(out[0].poTotal).toBe(2);

    expect(out[1].prefill_rows).toEqual([{ mpn: 'Y' }, { mpn: 'Z' }]);
    expect(out[1].image_b64).toBe('BBB');
  });

  it('grouped photos → concatenated pages + rows; first photo is the PO source', () => {
    const out = buildGroupPayloads(PHOTOS, [[0, 1]], 'generic');
    expect(out).toHaveLength(1);
    expect(out[0].pages).toEqual([{ image_b64: 'p0' }, { image_b64: 'p1' }]);
    expect(out[0].prefill_rows).toEqual([{ mpn: 'X' }, { mpn: 'Y' }, { mpn: 'Z' }]);
    expect(out[0].line_items).toEqual(out[0].prefill_rows);
    expect(out[0].image_b64).toBe('AAA');
    expect(out[0].poLabel).toBe('PO 1 of 1');
  });
});
