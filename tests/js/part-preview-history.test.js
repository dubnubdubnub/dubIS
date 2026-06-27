// @vitest-environment jsdom
/**
 * Unit tests for the part-preview History section.
 *
 * part-preview.js is a DOM module that relies on init() to create the tooltip
 * element and attach event listeners. We drive showTooltip by firing a
 * synthetic mouseover and advancing fake timers past the hover delay.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mocks must be declared before any import that transitively loads the module.
// vi.mock factories are hoisted — do not reference variables defined outside them.
vi.mock('../../js/api.js', () => ({
  api: vi.fn(),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
vi.mock('../../js/ui-helpers.js', () => ({
  escHtml: (s) => (s == null ? '' : String(s)),
}));

// Import the mocked api so we can call mockImplementation on it.
import { api as mockApi } from '../../js/api.js';
import { init } from '../../js/part-preview.js';

/** Minimal product payload that passes the renderTooltip guard. */
const MINIMAL_PRODUCT = {
  productCode: 'C12345',
  title: 'Test Capacitor',
  manufacturer: 'Acme',
  mpn: 'ACME-100',
  package: '0402',
  description: '100nF MLCC',
  stock: 1000,
  prices: [{ qty: 1, price: 0.01 }],
};

/** Adjustment history entries fixture. */
const HISTORY_ENTRIES = [
  { timestamp: '2024-01-10T10:00:00', kind: 'add', qty_delta: 100, source: 'import', note: '' },
  { timestamp: '2024-02-01T12:00:00', kind: 'consume', qty_delta: -5, source: 'openpnp', note: 'run 3' },
  { timestamp: '2024-03-15T08:30:00', kind: 'set', qty_delta: 90, source: 'manual', note: 'recount' },
];

/** Fire a synthetic mouseover over a data-lcsc element to trigger showTooltip. */
function fireLcscHover(code) {
  const el = document.querySelector(`[data-lcsc="${code}"]`);
  if (!el) throw new Error(`No element with data-lcsc="${code}"`);
  el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
}

/** Advance fake timers past hover delay and flush promise queue. */
async function advanceHoverDelay() {
  vi.advanceTimersByTime(400);
  // Flush multiple microtask rounds for chained promises
  for (let i = 0; i < 6; i++) await Promise.resolve();
}

describe('part-preview History section', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    vi.useFakeTimers();
    // Re-run init each test to get a fresh tooltip element
    init();

    // Add a part trigger element
    const trigger = document.createElement('span');
    trigger.setAttribute('data-lcsc', 'C12345');
    trigger.textContent = 'C12345';
    document.body.appendChild(trigger);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('renders "No adjustments recorded" when history is empty', async () => {
    mockApi.mockImplementation(async (method) => {
      if (method === 'fetch_lcsc_product') return MINIMAL_PRODUCT;
      if (method === 'get_price_summary') return {};
      if (method === 'get_part_history') return [];
      return null;
    });

    fireLcscHover('C12345');
    await advanceHoverDelay();

    const noHistory = document.querySelector('.part-preview-no-history');
    expect(noHistory).not.toBeNull();
    expect(noHistory.textContent).toBe('No adjustments recorded');
  });

  it('renders a row for each history entry', async () => {
    mockApi.mockImplementation(async (method) => {
      if (method === 'fetch_lcsc_product') return MINIMAL_PRODUCT;
      if (method === 'get_price_summary') return {};
      if (method === 'get_part_history') return HISTORY_ENTRIES;
      return null;
    });

    fireLcscHover('C12345');
    await advanceHoverDelay();

    const histSection = document.querySelector('.part-preview-adj-history');
    expect(histSection).not.toBeNull();
    const rows = histSection.querySelectorAll('tbody tr');
    expect(rows.length).toBe(3);
  });

  it('shows date, source, and qty delta in history rows', async () => {
    mockApi.mockImplementation(async (method) => {
      if (method === 'fetch_lcsc_product') return MINIMAL_PRODUCT;
      if (method === 'get_price_summary') return {};
      if (method === 'get_part_history') return HISTORY_ENTRIES;
      return null;
    });

    fireLcscHover('C12345');
    await advanceHoverDelay();

    const histSection = document.querySelector('.part-preview-adj-history');
    const rows = histSection.querySelectorAll('tbody tr');

    // add +100 on 2024-01-10
    expect(rows[0].textContent).toContain('2024-01-10');
    expect(rows[0].textContent).toContain('import');
    expect(rows[0].textContent).toContain('+100');

    // consume -5 from openpnp on 2024-02-01
    expect(rows[1].textContent).toContain('2024-02-01');
    expect(rows[1].textContent).toContain('openpnp');
    expect(rows[1].textContent).toContain('-5');

    // set kind renders as →90
    expect(rows[2].textContent).toContain('2024-03-15');
    expect(rows[2].textContent).toContain('manual');
    expect(rows[2].textContent).toContain('→90');
  });

  it('calls get_part_history with the LCSC code', async () => {
    mockApi.mockImplementation(async (method) => {
      if (method === 'fetch_lcsc_product') return MINIMAL_PRODUCT;
      if (method === 'get_price_summary') return {};
      if (method === 'get_part_history') return [];
      return null;
    });

    fireLcscHover('C12345');
    await advanceHoverDelay();

    const historyCalls = mockApi.mock.calls.filter(c => c[0] === 'get_part_history');
    expect(historyCalls.length).toBeGreaterThan(0);
    expect(historyCalls[0][1]).toBe('C12345');
  });

  it('shows History section title', async () => {
    mockApi.mockImplementation(async (method) => {
      if (method === 'fetch_lcsc_product') return MINIMAL_PRODUCT;
      if (method === 'get_price_summary') return {};
      if (method === 'get_part_history') return HISTORY_ENTRIES;
      return null;
    });

    fireLcscHover('C12345');
    await advanceHoverDelay();

    const histTitle = document.querySelector('.part-preview-adj-history .part-preview-history-title');
    expect(histTitle).not.toBeNull();
    expect(histTitle.textContent).toBe('History');
  });
});
