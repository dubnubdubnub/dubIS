import { describe, it, expect } from 'vitest';

import {
  PRESETS, PRESET_LABELS,
  money, unitPrice, qty, derivation, bestHint, note, candidateLabel,
  runnerUpDelta, summary, linesByRef, parseBoardCount,
} from '../../js/cart/cart-plan-logic.js';

/** A plan line shaped the way domain/cart_plan.py emits them. */
function line(overrides = {}) {
  return {
    ref: 'C1525',
    part_id: 'C1525',
    preset: 'min',
    board_count: 25,
    per_board_qty: 8,
    gross_qty: 200,
    covered_by_stock: 112,
    required_qty: 88,
    on_hand: 112,
    candidates: [],
    selected: null,
    runner_up: null,
    rejections: [],
    reason: '',
    over_ceiling: false,
    fell_back: '',
    ...overrides,
  };
}

function candidate(overrides = {}) {
  return {
    distributor: 'lcsc',
    packaging: 'Cut Tape',
    carrier: 'tape',
    is_reel: false,
    qty: 100,
    unit_price: 0.0041,
    fee: 0,
    spend: 0.41,
    break_qty: 100,
    on_break: true,
    surplus: 12,
    stock_known: true,
    origin: 'break',
    ...overrides,
  };
}

describe('preset vocabulary', () => {
  it('labels every preset the server understands', () => {
    for (const preset of PRESETS) {
      expect(PRESET_LABELS[preset]).toBeTruthy();
    }
  });
});

describe('money formatting', () => {
  it('always shows two decimals so a column of totals lines up', () => {
    expect(money(0.4)).toBe('$0.40');
    expect(money(1234.5)).toBe('$1,234.50');
  });

  it('renders a missing value as a dash, not $0.00', () => {
    // $0.00 is a price. Nothing is not.
    expect(money(null)).toBe('—');
    expect(money(undefined)).toBe('—');
    expect(money('nonsense')).toBe('—');
  });

  it('shows zero as a real zero', () => {
    expect(money(0)).toBe('$0.00');
  });
});

describe('unit-price formatting', () => {
  it('keeps enough precision for sub-cent passives', () => {
    // At two decimals a whole passives BOM renders as "$0.00".
    expect(unitPrice(0.00213)).toBe('$0.00213');
    expect(unitPrice(0.0041)).toBe('$0.00410');
  });

  it('uses four decimals once the price is above a cent', () => {
    expect(unitPrice(2.47)).toBe('$2.4700');
  });

  it('renders a missing unit price as a dash', () => {
    expect(unitPrice(null)).toBe('—');
  });
});

describe('quantity formatting', () => {
  it('separates thousands', () => {
    expect(qty(5000)).toBe('5,000');
  });

  it('shows zero rather than a dash', () => {
    expect(qty(0)).toBe('0');
  });

  it('renders a missing quantity as a dash', () => {
    expect(qty(null)).toBe('—');
  });
});

describe('derivation', () => {
  it('spells out the arithmetic behind a requirement', () => {
    // This string is the entire justification for storing board_count rather
    // than folding it into the quantity.
    expect(derivation(line()))
      .toBe('25 boards × 8 placements = 200, less 112 on hand');
  });

  it('singularises one board and one placement', () => {
    expect(derivation(line({ board_count: 1, per_board_qty: 1, gross_qty: 1, covered_by_stock: 0 })))
      .toBe('1 board × 1 placement = 1');
  });

  it('omits the stock clause when nothing is on hand', () => {
    expect(derivation(line({ covered_by_stock: 0 })))
      .toBe('25 boards × 8 placements = 200');
  });

  it('does not invent per-board arithmetic for a one-off line', () => {
    expect(derivation(line({ per_board_qty: null, gross_qty: 1, covered_by_stock: 0 })))
      .toBe('1 required');
  });

  it('survives a null line', () => {
    expect(derivation(null)).toBe('');
  });
});

describe('bestHint', () => {
  it('prefers the rejection that comes with a number to type', () => {
    const hint = bestHint([
      { reason: 'insufficient_stock', detail: 'only 41 in stock', nearest_legal: null },
      { reason: 'not_multiple', detail: 'sold in multiples of 5,000', nearest_legal: 5000 },
    ]);
    expect(hint.reason).toBe('not_multiple');
  });

  it('picks the smallest legal quantity among actionable rejections', () => {
    const hint = bestHint([
      { reason: 'below_ladder', detail: 'a', nearest_legal: 5000 },
      { reason: 'below_moq', detail: 'b', nearest_legal: 250 },
    ]);
    expect(hint.nearest_legal).toBe(250);
  });

  it('still returns something when nothing is actionable', () => {
    const hint = bestHint([{ reason: 'no_ladder', detail: 'no observed prices', nearest_legal: null }]);
    expect(hint.reason).toBe('no_ladder');
  });

  it('returns null for no rejections', () => {
    expect(bestHint([])).toBeNull();
    expect(bestHint(null)).toBeNull();
  });
});

describe('note', () => {
  it('says a line is covered by stock rather than leaving it blank', () => {
    expect(note(line({ required_qty: 0, reason: 'covered by stock on hand' })))
      .toBe('covered by stock on hand');
  });

  it('turns an actionable rejection into advice', () => {
    expect(note(line({
      selected: null,
      rejections: [{ reason: 'not_multiple', detail: 'sold in multiples of 5,000', nearest_legal: 5000 }],
    }))).toBe('sold in multiples of 5,000 — nearest is 5,000');
  });

  it('flags a pick that exceeds the reel ceiling', () => {
    expect(note(line({
      selected: candidate({ is_reel: true, spend: 90 }),
      over_ceiling: true,
      reason: 'cheapest reel available; exceeds the ceiling',
    }))).toContain('over your reel ceiling');
  });

  it('reports what a preset fell back to', () => {
    expect(note(line({
      selected: candidate(),
      fell_back: 'min',
      reason: 'no reel packaging offered for this part',
    }))).toBe('no reel packaging offered for this part');
  });

  it('says so when stock was never observed', () => {
    expect(note(line({ selected: candidate({ stock_known: false }) })))
      .toBe('stock not observed');
  });

  it('stays silent on an unremarkable row', () => {
    // A note on every row is a note nobody reads.
    expect(note(line({ selected: candidate() }))).toBe('');
  });
});

describe('candidateLabel', () => {
  it('pairs the quantity with its packaging', () => {
    expect(candidateLabel(candidate({ qty: 5000, packaging: 'Tape & Reel' })))
      .toBe('5,000 · Tape & Reel');
  });

  it('omits the separator when the packaging is unknown', () => {
    expect(candidateLabel(candidate({ qty: 100, packaging: '' }))).toBe('100');
  });

  it('renders nothing selected as a dash', () => {
    expect(candidateLabel(null)).toBe('—');
  });
});

describe('runnerUpDelta', () => {
  it('says what the next best option would have cost', () => {
    const alt = runnerUpDelta(line({
      selected: candidate({ spend: 2.29, qty: 694 }),
      runner_up: candidate({ spend: 2.9, qty: 1000 }),
    }));
    expect(alt.delta).toBeCloseTo(0.61, 5);
    expect(alt.label).toContain('1,000');
    expect(alt.label).toContain('$2.90');
  });

  it('returns null when there is no runner-up to compare against', () => {
    expect(runnerUpDelta(line({ selected: candidate() }))).toBeNull();
  });

  it('returns null when nothing was selected', () => {
    expect(runnerUpDelta(line({ selected: null, runner_up: candidate() }))).toBeNull();
  });
});

describe('summary', () => {
  it('reports the total and keeps the caveats beside it', () => {
    // A total that silently omits unpriced rows reads as complete.
    const s = summary({ totals: { spend: 412.5, lines: 111, covered_by_stock: 9, unpriced: 2 } });
    expect(s.spend).toBe('$412.50');
    expect(s.lines).toBe(111);
    expect(s.caveat).toBe('9 covered by stock, 2 unpriced');
  });

  it('has no caveat when every line priced', () => {
    expect(summary({ totals: { spend: 1, lines: 1, covered_by_stock: 0, unpriced: 0 } }).caveat).toBe('');
  });

  it('survives a missing plan', () => {
    expect(summary(null).spend).toBe('$0.00');
    expect(summary({}).lines).toBe(0);
  });
});

describe('linesByRef', () => {
  it('indexes lines so a row finds its plan without scanning', () => {
    const map = linesByRef({ lines: [line({ ref: 'a' }), line({ ref: 'b' })] });
    expect(map.get('b').ref).toBe('b');
    expect(map.size).toBe(2);
  });

  it('skips lines with no ref rather than keying them undefined', () => {
    expect(linesByRef({ lines: [line({ ref: '' }), null] }).size).toBe(0);
  });

  it('returns an empty map for a missing plan', () => {
    expect(linesByRef(null).size).toBe(0);
  });
});

describe('parseBoardCount', () => {
  it('accepts a positive whole number', () => {
    expect(parseBoardCount('25')).toBe(25);
    expect(parseBoardCount(' 7 ')).toBe(7);
  });

  it('rejects rather than rounding, because this multiplies every quantity', () => {
    for (const bad of ['0', '-3', '2.5', '', 'lots', '1e3', '٣']) {
      expect(parseBoardCount(bad)).toBeNull();
    }
  });

  it('rejects a number too large to be a safe integer', () => {
    expect(parseBoardCount('99999999999999999999')).toBeNull();
  });
});
