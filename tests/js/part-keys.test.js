import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));

import {
  bomKey, bomAggKey, invPartKey, rawRowAggKey,
  countStatuses, refColorClass, colorizeRefs, compressRefs,
  STATUS_ICONS, STATUS_ROW_CLASS, REF_COLOR_MAP,
} from '../../js/part-keys.js';

describe('bomKey', () => {
  it('returns uppercased LCSC number', () => {
    expect(bomKey({ lcsc: 'c123456', mpn: 'X' })).toBe('C123456');
  });

  it('falls back to MPN when no LCSC', () => {
    expect(bomKey({ lcsc: '', mpn: 'stm32f405' })).toBe('STM32F405');
  });

  it('returns empty string when no LCSC or MPN', () => {
    expect(bomKey({ lcsc: '', mpn: '' })).toBe('');
  });
});

describe('bomAggKey', () => {
  it('appends :DNP suffix for DNP parts', () => {
    expect(bomAggKey({ lcsc: 'C123', mpn: '', dnp: true })).toBe('C123:DNP');
  });

  it('no suffix for non-DNP parts', () => {
    expect(bomAggKey({ lcsc: 'C123', mpn: '', dnp: false })).toBe('C123');
  });
});

describe('invPartKey', () => {
  it('returns LCSC if it starts with C', () => {
    expect(invPartKey({ lcsc: 'C99999', mpn: 'X', digikey: 'Y' })).toBe('C99999');
  });

  it('falls back to MPN if LCSC does not start with C', () => {
    expect(invPartKey({ lcsc: 'notlcsc', mpn: 'ABC123', digikey: 'DK1' })).toBe('ABC123');
  });

  it('falls back to digikey if no LCSC or MPN', () => {
    expect(invPartKey({ lcsc: '', mpn: '', digikey: 'DK-PART' })).toBe('DK-PART');
  });

  it('returns empty string when nothing available', () => {
    expect(invPartKey({ lcsc: '', mpn: '', digikey: '' })).toBe('');
  });
});

describe('rawRowAggKey', () => {
  const baseCols = { lcsc: 0, mpn: 1, qty: 2, ref: 3, desc: -1, value: -1, footprint: -1, dnp: -1 };

  it('derives key from LCSC in row', () => {
    expect(rawRowAggKey(['C123456', 'MPN1', '1', 'R1'], baseCols)).toBe('C123456');
  });

  it('falls back to uppercased MPN', () => {
    expect(rawRowAggKey(['', 'stm32', '1', 'U1'], baseCols)).toBe('STM32');
  });

  it('returns empty string when no identifiers', () => {
    expect(rawRowAggKey(['', '', '1', 'X1'], baseCols)).toBe('');
  });

  it('appends :DNP when DNP column is set', () => {
    const cols = { ...baseCols, dnp: 4 };
    expect(rawRowAggKey(['C123', '', '1', 'R1', 'DNP'], cols)).toBe('C123:DNP');
  });
});

describe('countStatuses', () => {
  it('counts each status type', () => {
    const rows = [
      { effectiveStatus: 'ok' },
      { effectiveStatus: 'ok' },
      { effectiveStatus: 'short' },
      { effectiveStatus: 'missing' },
      { effectiveStatus: 'possible' },
      { effectiveStatus: 'manual', coveredByAlts: false },
      { effectiveStatus: 'confirmed', coveredByAlts: true },
      { effectiveStatus: 'dnp' },
    ];
    const c = countStatuses(rows);
    expect(c.ok).toBe(2);
    expect(c.short).toBe(1);
    expect(c.missing).toBe(1);
    expect(c.possible).toBe(1);
    expect(c.manual).toBe(1);
    expect(c.confirmed).toBe(1);
    expect(c.covered).toBe(1);
    expect(c.dnp).toBe(1);
    expect(c.total).toBe(8);
  });

  it('counts manual-short and confirmed-short under short + manual/confirmed', () => {
    const rows = [
      { effectiveStatus: 'manual-short' },
      { effectiveStatus: 'confirmed-short' },
    ];
    const c = countStatuses(rows);
    expect(c.short).toBe(2);
    expect(c.manual).toBe(1);
    expect(c.confirmed).toBe(1);
  });

  it('returns zeros for empty array', () => {
    const c = countStatuses([]);
    expect(c.total).toBe(0);
    expect(c.ok).toBe(0);
  });
});

describe('refColorClass', () => {
  it('maps R to ref-r', () => {
    expect(refColorClass('R1')).toBe('ref-r');
  });

  it('maps C to ref-c', () => {
    expect(refColorClass('C42')).toBe('ref-c');
  });

  it('maps U to ref-ic', () => {
    expect(refColorClass('U1')).toBe('ref-ic');
  });

  it('maps LED to ref-d', () => {
    expect(refColorClass('LED1')).toBe('ref-d');
  });

  it('returns empty string for unknown prefix', () => {
    expect(refColorClass('J1')).toBe('');
  });

  it('returns empty string for non-alpha input', () => {
    expect(refColorClass('123')).toBe('');
  });
});

describe('colorizeRefs', () => {
  it('wraps each ref in a span with color class', () => {
    const html = colorizeRefs('R1, C2');
    expect(html).toContain('class="ref-r"');
    expect(html).toContain('class="ref-c"');
    expect(html).toContain('R1');
    expect(html).toContain('C2');
  });

  it('returns empty string for empty input', () => {
    expect(colorizeRefs('')).toBe('');
    expect(colorizeRefs(null)).toBe('');
  });

  it('handles refs with no color class', () => {
    const html = colorizeRefs('J1');
    expect(html).toContain('J1');
    expect(html).not.toContain('class="ref-');
  });
});

describe('compressRefs', () => {
  it('returns empty string for empty input', () => {
    expect(compressRefs('')).toBe('');
    expect(compressRefs(null)).toBe('');
  });

  it('returns single ref unchanged', () => {
    expect(compressRefs('R1')).toBe('R1');
  });

  it('returns non-consecutive refs unchanged', () => {
    expect(compressRefs('C3, C6, C7')).toBe('C3, C6–C7');
  });

  it('compresses a full consecutive run', () => {
    expect(compressRefs('C31, C32, C33, C34, C35')).toBe('C31–C35');
  });

  it('compresses mixed consecutive and non-consecutive', () => {
    expect(compressRefs('C3, C6, C7, C16, C17, C18, C19, C20')).toBe('C3, C6–C7, C16–C20');
  });

  it('does not merge across different prefixes', () => {
    expect(compressRefs('R1, R2, C1, C2')).toBe('R1–R2, C1–C2');
  });

  it('handles large runs', () => {
    const refs = Array.from({ length: 50 }, (_, i) => `R${i + 1}`).join(', ');
    expect(compressRefs(refs)).toBe('R1–R50');
  });

  it('handles pair (2 consecutive) as range', () => {
    expect(compressRefs('L1, L2')).toBe('L1–L2');
  });

  it('preserves spacing after compression', () => {
    expect(compressRefs('U1,U2,U3')).toBe('U1–U3');
  });

  it('handles the 28-capacitor BOM fixture case', () => {
    const refs = 'C3, C6, C7, C16, C17, C18, C19, C20, C25, C26, C28, C29, C31, C32, C33, C34, C35, C36, C37, C38, C39, C40, C41, C42, C43, C44, C45, C46';
    expect(compressRefs(refs)).toBe('C3, C6–C7, C16–C20, C25–C26, C28–C29, C31–C46');
  });
});

describe('constants', () => {
  it('STATUS_ICONS has all expected keys', () => {
    expect(STATUS_ICONS.ok).toBe('+');
    expect(STATUS_ICONS.missing).toBe('\u2014');
    expect(STATUS_ICONS.dnp).toBe('\u2716');
  });

  it('STATUS_ROW_CLASS has matching keys', () => {
    for (const key of Object.keys(STATUS_ICONS)) {
      expect(STATUS_ROW_CLASS).toHaveProperty(key);
    }
  });
});
