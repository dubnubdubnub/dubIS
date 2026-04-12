import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  escHtml: vi.fn(s => s || ''),
  stockValueColor: vi.fn(() => 'var(--color-green)'),
  showToast: vi.fn(),
}));

import {
  renderPartRowHtml,
  renderFilterBarHtml,
} from '../../js/inventory/inventory-renderer.js';

describe('renderPartRowHtml', () => {
  it('includes data attributes matching the inventory item', () => {
    const item = {
      lcsc: 'C2040', mpn: 'USB-C-SMD', digikey: '', pololu: '', mouser: '',
      manufacturer: 'XKB', package: 'SMD', description: 'usb connector',
      qty: 10, unit_price: 0.50, ext_price: 5.00, section: 'Connectors',
    };
    const html = renderPartRowHtml(item, {
      hideDescs: false, isBomMode: false, isLinkSource: false,
      isReverseTarget: false, sectionKey: 'Connectors', threshold: 0,
      genericParts: [],
    });

    expect(html).toContain('data-lcsc="C2040"');
    expect(html).toContain('adj-btn');
    expect(html).toContain('USB-C-SMD');
  });

  it('renders multiple items with distinct data attributes', () => {
    const items = [
      { lcsc: 'C1111', mpn: 'R1', digikey: '', pololu: '', mouser: '', manufacturer: '', package: '', description: '', qty: 5, unit_price: 0.01, ext_price: 0.05, section: 'Passives' },
      { lcsc: 'C2222', mpn: 'R2', digikey: '', pololu: '', mouser: '', manufacturer: '', package: '', description: '', qty: 10, unit_price: 0.02, ext_price: 0.20, section: 'Passives' },
      { lcsc: 'C3333', mpn: 'C1', digikey: '', pololu: '', mouser: '', manufacturer: '', package: '', description: '', qty: 3, unit_price: 0.03, ext_price: 0.09, section: 'Passives' },
    ];
    const htmls = items.map(item => renderPartRowHtml(item, {
      hideDescs: false, isBomMode: false, isLinkSource: false,
      isReverseTarget: false, sectionKey: 'Passives', threshold: 0,
      genericParts: [],
    }));

    expect(htmls[0]).toContain('data-lcsc="C1111"');
    expect(htmls[1]).toContain('data-lcsc="C2222"');
    expect(htmls[2]).toContain('data-lcsc="C3333"');
    expect(htmls[0]).not.toContain('C2222');
    expect(htmls[1]).not.toContain('C1111');
  });
});

describe('renderFilterBarHtml', () => {
  it('renders filter buttons with correct counts', () => {
    const counts = {
      total: 8, ok: 5, short: 2, missing: 1, possible: 0,
      confirmed: 3, manual: 0, generic: 0, dnp: 0,
    };
    const html = renderFilterBarHtml(counts, 'all');
    expect(html).toContain('filter-btn');
    expect(html).toContain('5');
    expect(html).toContain('2');
    expect(html).toContain('1');
  });
});
