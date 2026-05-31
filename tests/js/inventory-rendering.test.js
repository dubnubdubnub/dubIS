// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  escHtml: vi.fn(s => s || ''),
  stockValueColor: vi.fn(() => 'var(--color-green)'),
  showToast: vi.fn(),
}));

vi.mock('../../js/store.js', () => ({
  store: { purchaseOrders: [], vendors: [], inventory: [] },
}));

const labelMock = { mode: false, selected: new Set() };
vi.mock('../../js/label-selection.js', () => ({
  isLabelMode: () => labelMock.mode,
  isSelected: (key) => labelMock.selected.has(key),
}));

import {
  renderPartRowHtml,
  renderFilterBarHtml,
  createBomRowElement,
} from '../../js/inventory/inventory-renderer.js';

beforeEach(() => {
  labelMock.mode = false;
  labelMock.selected = new Set();
});

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

describe('renderPartRowHtml — unit price column', () => {
  const baseOpts = {
    hideDescs: false, isBomMode: false, isLinkSource: false,
    isReverseTarget: false, sectionKey: 'Resistors', threshold: 50, genericParts: null,
  };

  it('renders $X.XX for prices ≥ $0.01', () => {
    const html = renderPartRowHtml({ mpn: 'A', qty: 10, unit_price: 0.05 }, baseOpts);
    expect(html).toMatch(/<span class="part-unit-price">\$0\.05<\/span>/);
  });

  it('renders $X.XXXX for sub-cent prices', () => {
    const html = renderPartRowHtml({ mpn: 'A', qty: 10, unit_price: 0.0034 }, baseOpts);
    expect(html).toMatch(/<span class="part-unit-price">\$0\.0034<\/span>/);
  });

  it('renders em-dash for missing/zero unit price', () => {
    const html = renderPartRowHtml({ mpn: 'A', qty: 10 }, baseOpts);
    expect(html).toMatch(/<span class="part-unit-price">—<\/span>/);
  });

  it('renders a section chip when sectionChip option is provided', () => {
    const html = renderPartRowHtml(
      { mpn: 'A', qty: 10, unit_price: 0.05 },
      { ...baseOpts, sectionChip: 'Resistors' }
    );
    expect(html).toMatch(/<span class="inv-section-chip">Resistors<\/span>/);
  });
});

describe('renderPartRowHtml — label-select mode', () => {
  const baseOpts = {
    hideDescs: false, isBomMode: true, isLinkSource: false,
    isReverseTarget: false, sectionKey: 'Connectors', threshold: 0, genericParts: [],
  };

  it('renders the normal action buttons (no checkbox) when label mode is OFF', () => {
    labelMock.mode = false;
    const html = renderPartRowHtml(
      { lcsc: 'C2040', mpn: 'USB-C', qty: 10, unit_price: 0.5 }, baseOpts);
    expect(html).toContain('adj-btn');
    expect(html).toContain('link-btn');
    expect(html).not.toContain('label-select-checkbox');
  });

  it('renders a checkbox in place of the action buttons when label mode is ON', () => {
    labelMock.mode = true;
    const html = renderPartRowHtml(
      { lcsc: 'C2040', mpn: 'USB-C', qty: 10, unit_price: 0.5 }, baseOpts);
    expect(html).toContain('label-select-checkbox');
    // Action buttons are swapped out
    expect(html).not.toContain('adj-btn');
    expect(html).not.toContain('link-btn');
  });

  it('checkbox carries the invPartKey as data-key', () => {
    labelMock.mode = true;
    const html = renderPartRowHtml(
      { lcsc: 'C2040', mpn: 'USB-C', qty: 10, unit_price: 0.5 }, baseOpts);
    expect(html).toContain('data-key="C2040"');
  });

  it('checkbox reflects isSelected — unchecked when not selected', () => {
    labelMock.mode = true;
    const html = renderPartRowHtml(
      { lcsc: 'C2040', mpn: 'USB-C', qty: 10, unit_price: 0.5 }, baseOpts);
    expect(html).not.toContain('checked');
  });

  it('checkbox reflects isSelected — checked when selected', () => {
    labelMock.mode = true;
    labelMock.selected = new Set(['C2040']);
    const html = renderPartRowHtml(
      { lcsc: 'C2040', mpn: 'USB-C', qty: 10, unit_price: 0.5 }, baseOpts);
    expect(html).toContain('checked');
  });
});

describe('createBomRowElement — label-select mode', () => {
  function baseDisplayData(overrides) {
    return Object.assign({
      partKey: 'C2040', invKey: 'C2040', status: 'ok', rowClass: 'row-green',
      icon: '+', dispLcsc: 'C2040', dispDigikey: '', dispPololu: '', dispMouser: '',
      dispMpn: 'USB-C', effectiveQty: 5, invQty: 10, invDesc: 'connector',
      matchLabel: 'exact', qtyClass: '', refs: 'J1', isMissing: false,
      altBadge: null, showConfirm: false, showUnconfirm: false, showAdjust: true,
      showLink: true, linkActive: false, isLinkingSource: false,
      isReverseLinkingSource: false, isReverseTarget: false, showAlts: false,
      showMembers: false, memberBadge: null, genericPartName: '', genericMembers: null,
      showGroupFlyout: false, genericPartId: null, bomValue: '', bomFootprint: '',
      bomRefs: 'J1', hasInv: true, footprintConfirmed: false, footprintCode: '',
    }, overrides);
  }

  it('renders action buttons (no checkbox) when label mode is OFF', () => {
    labelMock.mode = false;
    const tr = createBomRowElement(baseDisplayData());
    expect(tr.querySelector('.adj-btn')).not.toBeNull();
    expect(tr.querySelector('.link-btn')).not.toBeNull();
    expect(tr.querySelector('.label-select-checkbox')).toBeNull();
  });

  it('renders a checkbox keyed by invKey when label mode is ON', () => {
    labelMock.mode = true;
    const tr = createBomRowElement(baseDisplayData());
    const cb = tr.querySelector('.label-select-checkbox');
    expect(cb).not.toBeNull();
    expect(cb.dataset.key).toBe('C2040');
    expect(cb.checked).toBe(false);
    // Action buttons swapped out
    expect(tr.querySelector('.adj-btn')).toBeNull();
    expect(tr.querySelector('.link-btn')).toBeNull();
  });

  it('checkbox is checked when the invKey is selected', () => {
    labelMock.mode = true;
    labelMock.selected = new Set(['C2040']);
    const tr = createBomRowElement(baseDisplayData());
    const cb = tr.querySelector('.label-select-checkbox');
    expect(cb.checked).toBe(true);
  });

  it('missing BOM rows (no invKey) show no checkbox in label mode', () => {
    labelMock.mode = true;
    const tr = createBomRowElement(baseDisplayData({
      invKey: '', hasInv: false, isMissing: true, showAdjust: false, showLink: true,
    }));
    expect(tr.querySelector('.label-select-checkbox')).toBeNull();
  });
});
