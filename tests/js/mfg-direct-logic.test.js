import { describe, it, expect } from 'vitest';
import { canonicalizeUrl, emptyLineItem, validateLineItems, formatMatchBadge,
  mapScanLineItems, scanSourceFile }
  from '../../js/import/mfg-direct/mfg-direct-logic.js';

describe('canonicalizeUrl', () => {
  it('adds https scheme', () => {
    expect(canonicalizeUrl('Example.com')).toBe('https://example.com');
  });
  it('strips trailing slash', () => {
    expect(canonicalizeUrl('https://example.com/')).toBe('https://example.com');
  });
});

describe('validateLineItems', () => {
  it('flags empty mpn', () => {
    const items = [{ ...emptyLineItem(), quantity: 5 }];
    expect(validateLineItems(items).length).toBeGreaterThan(0);
  });
  it('flags zero qty', () => {
    const items = [{ ...emptyLineItem(), mpn: 'X' }];
    expect(validateLineItems(items).length).toBeGreaterThan(0);
  });
  it('passes valid', () => {
    const items = [{ ...emptyLineItem(), mpn: 'X', quantity: 5 }];
    expect(validateLineItems(items)).toEqual([]);
  });
});

describe('emptyLineItem', () => {
  it('includes distributor and distributor_pn fields', () => {
    const li = emptyLineItem();
    expect(li).toHaveProperty('distributor', '');
    expect(li).toHaveProperty('distributor_pn', '');
  });
});

describe('mapScanLineItems', () => {
  it('maps backend line items to editor shape with pending match', () => {
    const items = [
      { mpn: 'TMR2615', manufacturer: 'TMR', package: 'SOT23', quantity: 10,
        unit_price: 0.5, distributor: 'lcsc', distributor_pn: 'C12345' },
    ];
    const mapped = mapScanLineItems(items, 'lcsc');
    expect(mapped).toHaveLength(1);
    expect(mapped[0]).toMatchObject({
      mpn: 'TMR2615', manufacturer: 'TMR', package: 'SOT23',
      quantity: 10, unit_price: 0.5,
      distributor: 'lcsc', distributor_pn: 'C12345',
    });
    expect(mapped[0].match).toEqual({ status: 'pending' });
  });

  it('preserves distributor_pn for OCR rows', () => {
    const mapped = mapScanLineItems([{ mpn: 'X', distributor_pn: 'C999' }], 'lcsc');
    expect(mapped[0].distributor_pn).toBe('C999');
  });

  it('falls back distributor to template when item omits it', () => {
    const mapped = mapScanLineItems([{ mpn: 'X' }], 'digikey');
    expect(mapped[0].distributor).toBe('digikey');
  });

  it('defaults missing numeric fields', () => {
    const mapped = mapScanLineItems([{ mpn: 'X' }]);
    expect(mapped[0].quantity).toBe(0);
    expect(mapped[0].unit_price).toBe(0);
  });

  it('returns [] for null/empty input', () => {
    expect(mapScanLineItems(null)).toEqual([]);
    expect(mapScanLineItems([])).toEqual([]);
  });
});

describe('scanSourceFile', () => {
  it('builds sourceFile from image_b64 + filename', () => {
    const src = scanSourceFile({ image_b64: 'AAAA', filename: 'po.jpg' });
    expect(src).toEqual({ name: 'po.jpg', bytes: 'AAAA' });
  });
  it('defaults filename when absent', () => {
    const src = scanSourceFile({ image_b64: 'AAAA' });
    expect(src.name).toBe('scan.jpg');
  });
  it('returns null without image data', () => {
    expect(scanSourceFile({ filename: 'x.jpg' })).toBeNull();
    expect(scanSourceFile(null)).toBeNull();
  });
});

describe('formatMatchBadge', () => {
  it('definite', () => {
    expect(formatMatchBadge({ status: 'definite' }).cls).toBe('match-definite');
  });
  it('possible includes top candidate mpn', () => {
    const b = formatMatchBadge({ status: 'possible', candidates: [{ mpn: 'TMR2615' }] });
    expect(b.label).toContain('TMR2615');
  });
});
