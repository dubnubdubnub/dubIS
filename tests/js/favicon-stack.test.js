// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

// vi.mock is hoisted — define the mock state here with a factory
var _mockPOs = [];
var _mockVendors = [];

vi.mock('../../js/store.js', () => ({
  store: {
    get purchaseOrders() { return _mockPOs; },
    get vendors() { return _mockVendors; },
    get inventory() { return []; },
  },
}));

vi.mock('../../js/ui-helpers.js', () => ({
  escHtml: vi.fn(s => String(s || '')),
}));

import { renderFanStack, buildHoverFlyout } from '../../js/inventory/favicon-stack.js';

beforeEach(() => {
  _mockPOs = [];
  _mockVendors = [];
});

describe('renderFanStack', () => {
  it('returns empty string when part has no po_history and no primary_vendor_id', () => {
    var part = { lcsc: 'C123', mpn: 'RES', po_history: [] };
    expect(renderFanStack(part)).toBe('');
  });

  it('returns empty string when po_history is absent and no primary_vendor_id', () => {
    var part = { lcsc: 'C123', mpn: 'RES' };
    expect(renderFanStack(part)).toBe('');
  });

  it('renders fan stack with favicon_path icon when vendor has favicon_path', () => {
    _mockVendors = [{ id: 'v1', name: 'LCSC', favicon_path: 'data/lcsc-icon.ico', icon: '' }];
    _mockPOs = [{ po_id: 'PO-001', vendor_id: 'v1', purchase_date: '2025-01-01' }];
    var part = { lcsc: 'C123', mpn: 'RES', po_history: ['PO-001'] };
    var html = renderFanStack(part);
    expect(html).toContain('favicon-fan-stack');
    expect(html).toContain('fan-icon-img');
    expect(html).toContain('data/lcsc-icon.ico');
  });

  it('renders fan stack with emoji icon when vendor has icon', () => {
    _mockVendors = [{ id: 'v2', name: 'Self', icon: '🏠', favicon_path: '' }];
    _mockPOs = [{ po_id: 'PO-002', vendor_id: 'v2', purchase_date: '' }];
    var part = { lcsc: 'C456', mpn: 'CAP', po_history: ['PO-002'] };
    var html = renderFanStack(part);
    expect(html).toContain('favicon-fan-stack');
    expect(html).toContain('fan-icon-emoji');
    expect(html).toContain('🏠');
  });

  it('renders empty icon when vendor has no icon or favicon_path', () => {
    _mockVendors = [{ id: 'v3', name: 'Unknown', icon: '', favicon_path: '' }];
    _mockPOs = [{ po_id: 'PO-003', vendor_id: 'v3', purchase_date: '' }];
    var part = { lcsc: '', mpn: 'U1', po_history: ['PO-003'] };
    var html = renderFanStack(part);
    expect(html).toContain('fan-icon-empty');
  });

  it('deduplicates vendors across multiple POs from same vendor', () => {
    _mockVendors = [{ id: 'v1', name: 'LCSC', favicon_path: 'data/lcsc.ico', icon: '' }];
    _mockPOs = [
      { po_id: 'PO-001', vendor_id: 'v1', purchase_date: '2025-01-01' },
      { po_id: 'PO-002', vendor_id: 'v1', purchase_date: '2025-03-01' },
    ];
    var part = { lcsc: 'C789', mpn: 'IND', po_history: ['PO-001', 'PO-002'] };
    var html = renderFanStack(part);
    // Should only show one icon for v1
    var count = (html.match(/fan-icon-img/g) || []).length;
    expect(count).toBe(1);
  });

  it('shows +N overflow badge for more than 3 vendors', () => {
    _mockVendors = [
      { id: 'v1', name: 'V1', icon: 'A', favicon_path: '' },
      { id: 'v2', name: 'V2', icon: 'B', favicon_path: '' },
      { id: 'v3', name: 'V3', icon: 'C', favicon_path: '' },
      { id: 'v4', name: 'V4', icon: 'D', favicon_path: '' },
    ];
    _mockPOs = [
      { po_id: 'PO-1', vendor_id: 'v1' },
      { po_id: 'PO-2', vendor_id: 'v2' },
      { po_id: 'PO-3', vendor_id: 'v3' },
      { po_id: 'PO-4', vendor_id: 'v4' },
    ];
    var part = { lcsc: 'C1', mpn: 'M1', po_history: ['PO-1', 'PO-2', 'PO-3', 'PO-4'] };
    var html = renderFanStack(part);
    expect(html).toContain('fan-icon-extra');
    expect(html).toContain('+1');
  });

  it('falls back to primary_vendor_id when po_history is empty', () => {
    _mockVendors = [{ id: 'v_self', name: 'Self', icon: '🔧', favicon_path: '' }];
    _mockPOs = [];
    var part = { lcsc: 'C999', mpn: 'LED', po_history: [], primary_vendor_id: 'v_self' };
    var html = renderFanStack(part);
    expect(html).toContain('favicon-fan-stack');
    expect(html).toContain('🔧');
  });
});

describe('buildHoverFlyout', () => {
  it('returns div with "No purchase history" when po_history empty', () => {
    var part = { po_history: [] };
    var el = buildHoverFlyout(part);
    expect(el.tagName).toBe('DIV');
    expect(el.className).toContain('favicon-fan-flyout');
    expect(el.textContent).toContain('No purchase history');
  });

  it('renders PO rows with vendor name and date', () => {
    _mockVendors = [{ id: 'v1', name: 'LCSC', favicon_path: 'data/lcsc.ico', icon: '' }];
    _mockPOs = [{ po_id: 'PO-001', vendor_id: 'v1', purchase_date: '2025-01-15' }];
    var part = { po_history: ['PO-001'] };
    var el = buildHoverFlyout(part);
    expect(el.innerHTML).toContain('LCSC');
    expect(el.innerHTML).toContain('2025-01-15');
    expect(el.innerHTML).toContain('PO-001');
  });

  it('shows header with PO count', () => {
    _mockVendors = [{ id: 'v1', name: 'LCSC', favicon_path: '', icon: '' }];
    _mockPOs = [
      { po_id: 'PO-001', vendor_id: 'v1', purchase_date: '2025-01-01' },
      { po_id: 'PO-002', vendor_id: 'v1', purchase_date: '2025-06-01' },
    ];
    var part = { po_history: ['PO-001', 'PO-002'] };
    var el = buildHoverFlyout(part);
    expect(el.innerHTML).toContain('(2)');
  });

  it('skips unknown PO ids gracefully', () => {
    _mockPOs = [];
    _mockVendors = [];
    var part = { po_history: ['UNKNOWN-PO'] };
    var el = buildHoverFlyout(part);
    // Should render header with count but no rows (PO not found)
    expect(el.innerHTML).toContain('(1)');
    expect(el.querySelector('.flyout-po-row')).toBeNull();
  });
});
