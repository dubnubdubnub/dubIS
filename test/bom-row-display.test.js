import { describe, it, expect } from 'vitest';
import { bomRowDisplayData } from '../js/bom-row-data.js';

// ── Test helpers ──

function makeRow(overrides = {}) {
  return {
    bom: { lcsc: 'C12345', mpn: 'RC0805FR-07100KL', value: '100k', refs: 'R1, R2', desc: '100kΩ 0805', dnp: false },
    inv: { lcsc: 'C12345', mpn: 'RC0805FR-07100KL', description: '100kΩ Resistor 0805', qty: 200, unit_price: 0.01, ext_price: 2.00, digikey: 'DK-12345', package: '0805' },
    alts: [],
    effectiveStatus: 'ok',
    matchType: 'lcsc',
    effectiveQty: 10,
    altQty: 0,
    coveredByAlts: false,
    ...overrides,
  };
}

const NO_LINKING = { linkingMode: false, linkingInvItem: null, linkingBomRow: null };
const EMPTY_ALTS = new Set();

// ── Filter tests ──

describe('bomRowDisplayData — status filtering', () => {
  it('returns data when activeFilter is "all"', () => {
    const r = makeRow({ effectiveStatus: 'ok' });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d).not.toBeNull();
    expect(d.status).toBe('ok');
  });

  it('returns null when status does not match filter', () => {
    const r = makeRow({ effectiveStatus: 'ok' });
    expect(bomRowDisplayData(r, '', 'missing', EMPTY_ALTS, NO_LINKING)).toBeNull();
  });

  it('manual filter includes manual-short rows', () => {
    const r = makeRow({ effectiveStatus: 'manual-short' });
    const d = bomRowDisplayData(r, '', 'manual', EMPTY_ALTS, NO_LINKING);
    expect(d).not.toBeNull();
    expect(d.status).toBe('manual-short');
  });

  it('confirmed filter includes confirmed-short rows', () => {
    const r = makeRow({ effectiveStatus: 'confirmed-short' });
    const d = bomRowDisplayData(r, '', 'confirmed', EMPTY_ALTS, NO_LINKING);
    expect(d).not.toBeNull();
  });

  it('short filter includes manual-short and confirmed-short', () => {
    expect(bomRowDisplayData(makeRow({ effectiveStatus: 'manual-short' }), '', 'short', EMPTY_ALTS, NO_LINKING)).not.toBeNull();
    expect(bomRowDisplayData(makeRow({ effectiveStatus: 'confirmed-short' }), '', 'short', EMPTY_ALTS, NO_LINKING)).not.toBeNull();
  });
});

describe('bomRowDisplayData — search filtering', () => {
  it('returns data when query matches BOM fields', () => {
    const r = makeRow();
    expect(bomRowDisplayData(r, '100k', 'all', EMPTY_ALTS, NO_LINKING)).not.toBeNull();
  });

  it('returns data when query matches inventory fields', () => {
    const r = makeRow();
    expect(bomRowDisplayData(r, 'resistor', 'all', EMPTY_ALTS, NO_LINKING)).not.toBeNull();
  });

  it('returns null when query does not match', () => {
    const r = makeRow();
    expect(bomRowDisplayData(r, 'zzzznotfound', 'all', EMPTY_ALTS, NO_LINKING)).toBeNull();
  });

  it('skips search when query is empty', () => {
    const r = makeRow();
    expect(bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING)).not.toBeNull();
  });
});

// ── Display value tests ──

describe('bomRowDisplayData — display values', () => {
  it('computes partKey from bom', () => {
    const r = makeRow();
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.partKey).toBe('C12345');
  });

  it('prefers inventory LCSC over BOM LCSC', () => {
    const r = makeRow({
      bom: { lcsc: 'C99999', mpn: '', value: '', refs: '', desc: '' },
      inv: { lcsc: 'C12345', mpn: '', description: '', qty: 5, digikey: '' },
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.dispLcsc).toBe('C12345');
  });

  it('falls back to BOM LCSC when no inventory', () => {
    const r = makeRow({
      inv: null,
      effectiveStatus: 'missing',
      matchType: 'lcsc',
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.dispLcsc).toBe('C12345');
    expect(d.invDesc).toBe('100kΩ 0805');
  });

  it('shows "not in inventory" when no inv and no bom desc/value', () => {
    const r = makeRow({
      bom: { lcsc: 'C12345', mpn: '', value: '', refs: '', desc: '' },
      inv: null,
      effectiveStatus: 'missing',
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.invDesc).toBe('not in inventory');
  });

  it('returns correct match labels', () => {
    const types = { lcsc: 'LCSC', mpn: 'MPN', fuzzy: 'Fuzzy', value: 'Value', manual: 'Manual', confirmed: 'Confirmed' };
    for (const [type, label] of Object.entries(types)) {
      const r = makeRow({ matchType: type });
      const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
      expect(d.matchLabel).toBe(label);
    }
  });

  it('returns dash for unknown match type', () => {
    const r = makeRow({ matchType: 'unknown' });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.matchLabel).toBe('\u2014');
  });
});

// ── Row class and icon tests ──

describe('bomRowDisplayData — row class and icon', () => {
  it('maps ok status to row-green', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'ok' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.rowClass).toBe('row-green');
    expect(d.icon).toBe('+');
  });

  it('maps short+coveredByAlts to row-yellow-covered with ~+ icon', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'short', coveredByAlts: true }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.rowClass).toBe('row-yellow-covered');
    expect(d.icon).toBe('~+');
  });

  it('maps missing to row-red', () => {
    const r = makeRow({ effectiveStatus: 'missing', inv: null });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.rowClass).toBe('row-red');
  });

  it('maps dnp to row-dnp', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'dnp' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.rowClass).toBe('row-dnp');
  });
});

// ── Qty class tests ──

describe('bomRowDisplayData — quantity CSS class', () => {
  it('ok → qty-ok', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'ok' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.qtyClass).toBe('qty-ok');
  });

  it('short covered → qty-ok', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'short', coveredByAlts: true }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.qtyClass).toBe('qty-ok');
  });

  it('short not covered → qty-short', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'short', coveredByAlts: false }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.qtyClass).toBe('qty-short');
  });

  it('possible → qty-possible', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'possible' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.qtyClass).toBe('qty-possible');
  });

  it('missing → qty-miss', () => {
    const r = makeRow({ effectiveStatus: 'missing', inv: null });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.qtyClass).toBe('qty-miss');
  });

  it('manual → qty-manual', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'manual' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.qtyClass).toBe('qty-manual');
  });

  it('confirmed-short → qty-confirmed-short', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'confirmed-short' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.qtyClass).toBe('qty-confirmed-short');
  });
});

// ── Button visibility tests ──

describe('bomRowDisplayData — button visibility', () => {
  it('shows confirm for possible with inventory', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'possible' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.showConfirm).toBe(true);
    expect(d.showUnconfirm).toBe(false);
  });

  it('shows unconfirm for confirmed with inventory', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'confirmed' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.showConfirm).toBe(false);
    expect(d.showUnconfirm).toBe(true);
  });

  it('shows unconfirm for confirmed-short with inventory', () => {
    const d = bomRowDisplayData(makeRow({ effectiveStatus: 'confirmed-short' }), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.showUnconfirm).toBe(true);
  });

  it('hides confirm/unconfirm when no inventory', () => {
    const r = makeRow({ effectiveStatus: 'possible', inv: null });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.showConfirm).toBe(false);
    expect(d.showUnconfirm).toBe(false);
  });

  it('shows adjust only when inventory exists', () => {
    expect(bomRowDisplayData(makeRow(), '', 'all', EMPTY_ALTS, NO_LINKING).showAdjust).toBe(true);
    const r = makeRow({ inv: null, effectiveStatus: 'missing' });
    expect(bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING).showAdjust).toBe(false);
  });

  it('shows link for rows with inventory or missing status', () => {
    expect(bomRowDisplayData(makeRow(), '', 'all', EMPTY_ALTS, NO_LINKING).showLink).toBe(true);
    const r = makeRow({ inv: null, effectiveStatus: 'missing' });
    expect(bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING).showLink).toBe(true);
    const r2 = makeRow({ inv: null, effectiveStatus: 'dnp' });
    expect(bomRowDisplayData(r2, '', 'all', EMPTY_ALTS, NO_LINKING).showLink).toBe(false);
  });
});

// ── Alt badge tests ──

describe('bomRowDisplayData — alt badge', () => {
  it('returns null altBadge when no alts', () => {
    const d = bomRowDisplayData(makeRow(), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.altBadge).toBeNull();
  });

  it('computes alt badge for non-short status', () => {
    const r = makeRow({
      alts: [{ lcsc: 'C99', mpn: 'ALT1', description: 'alt part', qty: 50, package: '0805' }],
      altQty: 50,
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.altBadge).toEqual({
      altQty: 50,
      badgeText: '1 alt',
      covered: true,
      expanded: false,
    });
  });

  it('shows "2 alts" for multiple alternatives', () => {
    const r = makeRow({
      alts: [
        { lcsc: 'C99', mpn: 'ALT1', description: 'a', qty: 25, package: '0805' },
        { lcsc: 'C88', mpn: 'ALT2', description: 'b', qty: 25, package: '0805' },
      ],
      altQty: 50,
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.altBadge.badgeText).toBe('2 alts');
  });

  it('shows "✔ covers" for short+covered status', () => {
    const r = makeRow({
      effectiveStatus: 'short',
      coveredByAlts: true,
      alts: [{ lcsc: 'C99', mpn: '', description: '', qty: 20, package: '' }],
      altQty: 20,
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.altBadge.badgeText).toBe('\u2714 covers');
    expect(d.altBadge.covered).toBe(true);
  });

  it('shows "still short" for short+not covered', () => {
    const r = makeRow({
      effectiveStatus: 'short',
      coveredByAlts: false,
      alts: [{ lcsc: 'C99', mpn: '', description: '', qty: 5, package: '' }],
      altQty: 5,
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.altBadge.badgeText).toBe('still short');
    expect(d.altBadge.covered).toBe(false);
  });

  it('tracks expanded state', () => {
    const r = makeRow({
      alts: [{ lcsc: 'C99', mpn: '', description: '', qty: 10, package: '' }],
      altQty: 10,
    });
    const expanded = new Set(['C12345']);
    const d = bomRowDisplayData(r, '', 'all', expanded, NO_LINKING);
    expect(d.altBadge.expanded).toBe(true);
    expect(d.showAlts).toBe(true);
  });
});

// ── Linking state tests ──

describe('bomRowDisplayData — linking state', () => {
  it('marks forward linking source', () => {
    const r = makeRow();
    const linking = { linkingMode: true, linkingInvItem: r.inv, linkingBomRow: null };
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, linking);
    expect(d.isLinkingSource).toBe(true);
    expect(d.linkActive).toBe(true);
  });

  it('marks reverse linking source (missing BOM row)', () => {
    const r = makeRow({ inv: null, effectiveStatus: 'missing' });
    const linking = { linkingMode: true, linkingInvItem: null, linkingBomRow: r };
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, linking);
    expect(d.isReverseLinkingSource).toBe(true);
    expect(d.linkActive).toBe(true);
  });

  it('marks reverse link targets (inv rows during reverse linking)', () => {
    const bomRow = makeRow({ inv: null, effectiveStatus: 'missing', bom: { lcsc: 'C99999', mpn: '', value: '', refs: '', desc: '' } });
    const invRow = makeRow({ effectiveStatus: 'ok' });
    const linking = { linkingMode: true, linkingInvItem: null, linkingBomRow: bomRow };
    const d = bomRowDisplayData(invRow, '', 'all', EMPTY_ALTS, linking);
    expect(d.isReverseTarget).toBe(true);
    expect(d.isReverseLinkingSource).toBe(false);
  });

  it('does not mark reverse target when not in linking mode', () => {
    const r = makeRow();
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.isReverseTarget).toBe(false);
  });
});

// ── Edge cases ──

describe('bomRowDisplayData — edge cases', () => {
  it('handles row with no inventory and no BOM desc', () => {
    const r = makeRow({
      bom: { lcsc: '', mpn: '', value: '', refs: '', desc: '' },
      inv: null,
      effectiveStatus: 'missing',
    });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.invDesc).toBe('not in inventory');
    expect(d.dispLcsc).toBe('');
    expect(d.dispMpn).toBe('');
  });

  it('returns invQty as dash when no inventory', () => {
    const r = makeRow({ inv: null, effectiveStatus: 'missing' });
    const d = bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.invQty).toBe('\u2014');
  });

  it('returns numeric invQty when inventory exists', () => {
    const d = bomRowDisplayData(makeRow(), '', 'all', EMPTY_ALTS, NO_LINKING);
    expect(d.invQty).toBe(200);
  });

  it('hasInv reflects inventory presence', () => {
    expect(bomRowDisplayData(makeRow(), '', 'all', EMPTY_ALTS, NO_LINKING).hasInv).toBe(true);
    const r = makeRow({ inv: null, effectiveStatus: 'missing' });
    expect(bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING).hasInv).toBe(false);
  });

  it('isMissing reflects missing status', () => {
    const r = makeRow({ inv: null, effectiveStatus: 'missing' });
    expect(bomRowDisplayData(r, '', 'all', EMPTY_ALTS, NO_LINKING).isMissing).toBe(true);
    expect(bomRowDisplayData(makeRow(), '', 'all', EMPTY_ALTS, NO_LINKING).isMissing).toBe(false);
  });
});
