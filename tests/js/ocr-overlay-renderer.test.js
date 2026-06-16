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
    tokenMode: 'w',
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

  it('renders the Words/Lines mode toggle with active state from tokenMode', () => {
    const html = renderModal(makeState());
    expect(html).toContain('id="ocr-mode-words"');
    expect(html).toContain('id="ocr-mode-lines"');
    // In word mode, the Words button is active/pressed and Lines is not.
    expect(html).toMatch(/id="ocr-mode-words"[^>]*class="[^"]*\bactive\b/);
    expect(html).toMatch(/id="ocr-mode-words"[^>]*aria-pressed="true"/);
    expect(html).toMatch(/id="ocr-mode-lines"[^>]*aria-pressed="false"/);
  });

  it('in tokenMode "l" renders one .ocr-token per line with :l: ids', () => {
    const state = makeState();
    state.tokenMode = 'l';
    state.pages[0].lines = [
      { text: 'C123 STM32', x: 100, y: 200, w: 200, h: 20, conf: 0.9 },
      { text: 'LQFP48', x: 100, y: 240, w: 120, h: 20, conf: 0.8 },
    ];
    const html = renderModal(state);
    const tokens = html.match(/class="ocr-token"/g) || [];
    expect(tokens.length).toBe(2);
    expect(html).toContain('data-token="0:l:0"');
    expect(html).toContain('data-token="0:l:1"');
    // No word tokens should be rendered in line mode.
    expect(html).not.toContain('data-token="0:w:0"');
    // Lines button is now the active one.
    expect(html).toMatch(/id="ocr-mode-lines"[^>]*class="[^"]*\bactive\b/);
    expect(html).toMatch(/id="ocr-mode-lines"[^>]*aria-pressed="true"/);
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

describe('renderModal loading skeleton', () => {
  const loading = (images, imageIdx = 0) => ({
    loading: true, images, imageIdx,
    template: '', pages: [], pageIdx: 0, rows: [], lowConf: [],
    pending: { kind: null, tokenIds: [], cell: null }, tokenMode: 'w', zoom: 1,
  });

  it('renders the overlay shell with the loading class and scan sweep', () => {
    const html = renderModal(loading([{ b64: 'AAAA', name: 'po.png' }]));
    expect(html).toContain('id="ocr-overlay"');
    expect(html).toContain('ocr-loading');
    expect(html).toContain('ocr-scan-sweep');
    expect(html).toContain('aria-busy="true"');
  });

  it('shows the dropped image under the sweep when bytes are present', () => {
    const html = renderModal(loading([{ b64: 'AAAA', name: 'po.png' }]));
    expect(html).toContain('class="ocr-skel-img"');
    expect(html).toContain('data:image/png;base64,AAAA');
    expect(html).not.toContain('ocr-skel-doc');
  });

  it('falls back to a document placeholder when no image bytes', () => {
    const html = renderModal(loading([]));
    expect(html).toContain('ocr-skel-doc');
    expect(html).not.toContain('class="ocr-skel-img"');
  });

  it('status reads "Reading image…" for a single image', () => {
    expect(renderModal(loading([{ b64: 'A', name: 'a.png' }]))).toContain('Reading image…');
  });

  it('status counts "Reading image N of M…" for multiple images', () => {
    const imgs = [{ b64: 'A', name: 'a' }, { b64: 'B', name: 'b' }, { b64: 'C', name: 'c' }];
    expect(renderModal(loading(imgs, 0))).toContain('Reading image 1 of 3…');
  });

  it('renders shimmering skeleton rows and a disabled confirm + working cancel', () => {
    const html = renderModal(loading([{ b64: 'A', name: 'a' }]));
    expect((html.match(/class="ocr-skel-row"/g) || []).length).toBeGreaterThanOrEqual(3);
    expect(html).toMatch(/id="ocr-confirm"[^>]*disabled/);
    expect(html).toContain('id="ocr-cancel"');
    expect(html).not.toContain('ocr-token');
    expect(html).not.toContain('ocr-vendor-mount');
  });

  it('escapes the image b64 so it cannot break out of the data URL', () => {
    const html = renderModal(loading([{ b64: '"><img onerror=x>', name: 'x' }]));
    expect(html).not.toContain('"><img onerror=x>');
  });
});
