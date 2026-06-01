// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { renderModal, gridFields } from '../../js/import/mfg-direct/ocr-overlay/ocr-overlay-renderer.js';

describe('gridFields', () => {
  it('lcsc prepends a distributor_pn column labeled LCSC#', () => {
    const fields = gridFields('lcsc');
    expect(fields[0]).toEqual({ key: 'distributor_pn', label: 'LCSC#' });
    expect(fields.map(f => f.key)).toContain('mpn');
  });

  it('generic has no distributor_pn column', () => {
    const fields = gridFields('generic');
    expect(fields.find(f => f.key === 'distributor_pn')).toBeUndefined();
    expect(fields[0].key).toBe('mpn');
  });

  it('each distributor template labels its own dist-PN column', () => {
    expect(gridFields('digikey')[0].label).toBe('DigiKey#');
    expect(gridFields('mouser')[0].label).toBe('Mouser#');
    expect(gridFields('pololu')[0].label).toBe('Pololu#');
  });
});

function makeState() {
  return {
    template: 'lcsc',
    pages: [{
      image_b64: 'AAAA',
      width: 1000,
      height: 2000,
      words: [
        { text: 'C123', x: 100, y: 200, w: 80, h: 20, conf: 0.9, line_id: 0 },
        { text: 'STM32', x: 100, y: 240, w: 120, h: 20, conf: 0.8, line_id: 1 },
      ],
      lines: [],
    }],
    pageIdx: 0,
    rows: [
      { distributor_pn: 'C123', mpn: 'STM32', manufacturer: 'ST', description: 'MCU', package: 'LQFP', quantity: 5, unit_price: 1.2 },
      { distributor_pn: '', mpn: '', manufacturer: '', description: '', package: '', quantity: 0, unit_price: 0 },
    ],
    lowConf: [new Set(['manufacturer']), new Set()],
    pending: { kind: null, tokenIds: [], cell: null },
  };
}

describe('renderModal', () => {
  it('returns a string with the overlay id, the scan image and grid', () => {
    const html = renderModal(makeState());
    expect(typeof html).toBe('string');
    expect(html).toContain('id="ocr-overlay"');
    expect(html).toContain('<img');
    expect(html).toContain('data:image/png;base64,AAAA');
  });

  it('renders one .ocr-token per word with percentage positions', () => {
    const html = renderModal(makeState());
    const tokens = html.match(/class="ocr-token"/g) || [];
    expect(tokens.length).toBe(2);
    // 100/1000 = 10% left, 200/2000 = 10% top
    expect(html).toContain('left:10%;top:10%');
    expect(html).toContain('data-token="0:w:0"');
  });

  it('renders one .ocr-cell per field per row', () => {
    const state = makeState();
    const html = renderModal(state);
    const cells = html.match(/class="ocr-cell[^"]*"/g) || [];
    expect(cells.length).toBe(gridFields(state.template).length * state.rows.length);
  });

  it('marks low-conf and blank cells', () => {
    const html = renderModal(makeState());
    expect(html).toContain('low-conf'); // manufacturer in row 0
    expect(html).toContain('blank');    // empty cells in row 1
  });

  it('escapes OCR token text', () => {
    const state = makeState();
    state.pages[0].words[0].text = '<script>x</script>';
    const html = renderModal(state);
    expect(html).not.toContain('<script>x</script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('shows page nav only for multi-page docs', () => {
    const single = makeState();
    expect(renderModal(single)).not.toContain('id="ocr-prev"');
    const multi = makeState();
    multi.pages.push({ ...multi.pages[0], words: [], lines: [] });
    expect(renderModal(multi)).toContain('id="ocr-prev"');
  });
});
