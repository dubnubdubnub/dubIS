import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));

import {
  renderDropZone,
  renderLoadedDropZone,
  renderBomSummary,
  renderPriceInfo,
  renderLinkingBanner,
  renderStagingHead,
  renderStagingRow,
} from '../../js/bom/bom-renderer.js';

describe('renderDropZone', () => {
  it('returns HTML with drop zone', () => {
    const html = renderDropZone();
    expect(html).toContain('id="bom-drop-zone"');
    expect(html).toContain('Drop a BOM CSV here');
    expect(html).toContain('id="bom-file-input"');
  });

  it('returns HTML with results container', () => {
    const html = renderDropZone();
    expect(html).toContain('id="bom-results"');
    expect(html).toContain('id="bom-summary"');
    expect(html).toContain('id="bom-qty-mult"');
    expect(html).toContain('id="bom-thead"');
    expect(html).toContain('id="bom-tbody"');
  });

  it('results container starts hidden', () => {
    const html = renderDropZone();
    expect(html).toContain('class="hidden"');
  });
});

describe('renderLoadedDropZone', () => {
  it('shows the filename', () => {
    const html = renderLoadedDropZone('test.csv');
    expect(html).toContain('test.csv');
    expect(html).toContain('Loaded');
  });

  it('contains hidden file input', () => {
    const html = renderLoadedDropZone('test.csv');
    expect(html).toContain('id="bom-file-input"');
    expect(html).toContain('display:none');
  });
});

describe('renderBomSummary', () => {
  const counts = {
    total: 20, ok: 10, short: 3, possible: 2, missing: 5,
    manual: 1, confirmed: 2, dnp: 3, covered: 1,
  };

  it('includes filename', () => {
    const html = renderBomSummary(counts, 'my-bom.csv', 1);
    expect(html).toContain('my-bom.csv');
  });

  it('includes correct chip counts', () => {
    const html = renderBomSummary(counts, 'bom.csv', 1);
    expect(html).toContain('20 unique');
    expect(html).toContain('10 ok');
    expect(html).toContain('3 short');
    expect(html).toContain('2 possible');
    expect(html).toContain('5 missing');
  });

  it('shows manual and confirmed chips when > 0', () => {
    const html = renderBomSummary(counts, 'bom.csv', 1);
    expect(html).toContain('1 manual');
    expect(html).toContain('2 confirmed');
  });

  it('hides manual chip when 0', () => {
    const html = renderBomSummary({ ...counts, manual: 0 }, 'bom.csv', 1);
    expect(html).not.toContain('manual');
  });

  it('shows DNP chip when > 0', () => {
    const html = renderBomSummary(counts, 'bom.csv', 1);
    expect(html).toContain('3 DNP');
  });

  it('hides DNP chip when 0', () => {
    const html = renderBomSummary({ ...counts, dnp: 0 }, 'bom.csv', 1);
    expect(html).not.toContain('DNP');
  });

  it('shows covered chip when > 0', () => {
    const html = renderBomSummary(counts, 'bom.csv', 1);
    expect(html).toContain('1 covered');
  });

  it('shows multiplier label when > 1', () => {
    const html = renderBomSummary(counts, 'bom.csv', 5);
    expect(html).toContain('(x5)');
  });

  it('hides multiplier label when 1', () => {
    const html = renderBomSummary(counts, 'bom.csv', 1);
    expect(html).not.toContain('(x1)');
  });
});

describe('renderPriceInfo', () => {
  it('returns price per board', () => {
    expect(renderPriceInfo(12.50, 12.50, 1)).toBe('$12.50/board');
  });

  it('returns price per board and total when multiplier > 1', () => {
    const text = renderPriceInfo(12.50, 37.50, 3);
    expect(text).toContain('$12.50/board');
    expect(text).toContain('$37.50 total');
  });

  it('returns empty string when price is 0', () => {
    expect(renderPriceInfo(0, 0, 1)).toBe('');
  });

  it('omits total when multiplier is 1', () => {
    const text = renderPriceInfo(5.00, 5.00, 1);
    expect(text).not.toContain('total');
  });
});

describe('renderLinkingBanner', () => {
  it('returns empty string when not active', () => {
    expect(renderLinkingBanner({ active: false, invItem: null, bomRow: null })).toBe('');
  });

  it('returns empty string when active but no item or row', () => {
    expect(renderLinkingBanner({ active: true, invItem: null, bomRow: null })).toBe('');
  });

  it('returns banner for invItem linking', () => {
    const html = renderLinkingBanner({
      active: true,
      invItem: { lcsc: 'C12345', mpn: '', description: '' },
      bomRow: null,
    });
    expect(html).toContain('linking-banner');
    expect(html).toContain('C12345');
    expect(html).toContain('cancel-link-btn');
  });

  it('returns banner for bomRow linking', () => {
    const html = renderLinkingBanner({
      active: true,
      invItem: null,
      bomRow: { bom: { lcsc: '', mpn: 'STM32F405', value: '' } },
    });
    expect(html).toContain('linking-banner');
    expect(html).toContain('STM32F405');
  });
});

describe('renderStagingHead', () => {
  it('returns thead with delete column and status column', () => {
    const html = renderStagingHead(['LCSC', 'MPN', 'Qty']);
    expect(html).toContain('<tr>');
    expect(html).toContain('row-delete');
    expect(html).toContain('LCSC');
    expect(html).toContain('MPN');
    expect(html).toContain('Qty');
  });
});

describe('renderStagingRow', () => {
  const cols = { lcsc: 0, mpn: 1, qty: 2, ref: -1, desc: -1, value: -1, footprint: -1, dnp: -1 };
  const headers = ['LCSC', 'MPN', 'Qty'];

  it('returns tr element with correct data-ri', () => {
    const html = renderStagingRow(['C123', 'ABC', '5'], 3, cols, headers, null, false, 'ok');
    expect(html).toContain('data-ri="3"');
    expect(html).toContain('<tr');
    expect(html).toContain('</tr>');
  });

  it('applies row-warn class for warn classification', () => {
    const html = renderStagingRow(['', '', '1'], 0, cols, headers, null, false, 'warn');
    expect(html).toContain('row-warn');
  });

  it('applies row-subtotal class for subtotal', () => {
    const html = renderStagingRow(['', '', 'Subtotal'], 0, cols, headers, null, false, 'subtotal');
    expect(html).toContain('row-subtotal');
  });

  it('applies row-dnp class for DNP', () => {
    const html = renderStagingRow(['C123', '', '1'], 0, cols, headers, null, false, 'dnp');
    expect(html).toContain('row-dnp');
  });

  it('applies status row class for ok rows with status', () => {
    const html = renderStagingRow(['C123', '', '1'], 0, cols, headers, 'ok', false, 'ok');
    expect(html).toContain('row-green');
  });

  it('applies status row class for missing status', () => {
    const html = renderStagingRow(['C123', '', '1'], 0, cols, headers, 'missing', false, 'ok');
    expect(html).toContain('row-red');
  });

  it('adds link-target class and data attributes when linkable', () => {
    const html = renderStagingRow(['C123', '', '1'], 0, cols, headers, 'missing', true, 'ok');
    expect(html).toContain('link-target');
    expect(html).toContain('data-action="link"');
    expect(html).toContain('data-agg-key');
  });

  it('includes delete button with data-action', () => {
    const html = renderStagingRow(['C123', '', '1'], 5, cols, headers, null, false, 'ok');
    expect(html).toContain('data-action="delete"');
    expect(html).toContain('data-ri="5"');
  });

  it('adds data-lcsc attribute for LCSC values', () => {
    const html = renderStagingRow(['C12345', 'X', '1'], 0, cols, headers, null, false, 'ok');
    expect(html).toContain('data-lcsc="C12345"');
  });

  it('does not add data-lcsc for non-LCSC values', () => {
    const html = renderStagingRow(['notlcsc', 'X', '1'], 0, cols, headers, null, false, 'ok');
    expect(html).not.toContain('data-lcsc');
  });

  it('renders ref column with colorized display when ref col is set', () => {
    const refCols = { ...cols, ref: 3 };
    const refHeaders = ['LCSC', 'MPN', 'Qty', 'Designator'];
    const html = renderStagingRow(['C123', '', '1', 'R1, C2'], 0, refCols, refHeaders, null, false, 'ok');
    expect(html).toContain('refs-cell');
    expect(html).toContain('data-action="show-input"');
  });

  it('includes input fields with data-ci for each column', () => {
    const html = renderStagingRow(['C123', 'ABC', '5'], 0, cols, headers, null, false, 'ok');
    expect(html).toContain('data-ci="0"');
    expect(html).toContain('data-ci="1"');
    expect(html).toContain('data-ci="2"');
  });
});
