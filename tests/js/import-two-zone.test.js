// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import {
  isOcrFile,
  seedManualRows,
  TARGET_FIELDS,
  PO_TEMPLATES,
} from '../../js/import/import-logic.js';
import { renderDropZone, renderOcrEngineNotice, TESSERACT_WINGET_COMMAND } from '../../js/import/import-renderer.js';

describe('isOcrFile — drop-zone routing predicate', () => {
  it('returns true for image / PDF names', () => {
    expect(isOcrFile('invoice.png')).toBe(true);
    expect(isOcrFile('photo.jpg')).toBe(true);
    expect(isOcrFile('photo.jpeg')).toBe(true);
    expect(isOcrFile('packing.pdf')).toBe(true);
    // case-insensitive
    expect(isOcrFile('SCAN.PNG')).toBe(true);
    expect(isOcrFile('Receipt.JPEG')).toBe(true);
  });

  it('returns false for CSV / spreadsheet / text names', () => {
    expect(isOcrFile('order.csv')).toBe(false);
    expect(isOcrFile('order.tsv')).toBe(false);
    expect(isOcrFile('notes.txt')).toBe(false);
    expect(isOcrFile('cart.xls')).toBe(false);
    expect(isOcrFile('cart.xlsx')).toBe(false);
  });

  it('returns false for empty / missing names', () => {
    expect(isOcrFile('')).toBe(false);
    expect(isOcrFile(undefined)).toBe(false);
    expect(isOcrFile(null)).toBe(false);
  });

  it('only matches the extension, not a substring', () => {
    expect(isOcrFile('png-export.csv')).toBe(false);
    expect(isOcrFile('my.pdf.csv')).toBe(false);
  });
});

describe('seedManualRows — blank manual-entry seed', () => {
  it('produces exactly one blank row over the generic headers', () => {
    const { parsedHeaders, parsedRows } = seedManualRows(PO_TEMPLATES.generic);
    expect(parsedHeaders).toEqual(PO_TEMPLATES.generic.headers);
    expect(parsedRows).toHaveLength(1);
    expect(parsedRows[0]).toHaveLength(parsedHeaders.length);
    expect(parsedRows[0].every(cell => cell === '')).toBe(true);
  });

  it('builds an identity mapping for headers that are valid target fields', () => {
    const { parsedHeaders, columnMapping } = seedManualRows(PO_TEMPLATES.generic);
    parsedHeaders.forEach((h, i) => {
      if (TARGET_FIELDS.includes(h)) {
        expect(columnMapping[i]).toBe(h);
      } else {
        expect(columnMapping[i]).toBeUndefined();
      }
    });
    // Every generic header is a known target field → fully mapped.
    expect(Object.keys(columnMapping)).toHaveLength(parsedHeaders.length);
  });

  it('defaults to the generic template when called with no args', () => {
    const seed = seedManualRows();
    expect(seed.parsedHeaders).toEqual(PO_TEMPLATES.generic.headers);
    expect(seed.parsedRows).toHaveLength(1);
  });

  it('does not share array references with the template (deep copy of headers)', () => {
    const seed = seedManualRows(PO_TEMPLATES.generic);
    expect(seed.parsedHeaders).not.toBe(PO_TEMPLATES.generic.headers);
  });
});

describe('renderDropZone — two-zone layout', () => {
  const html = renderDropZone(PO_TEMPLATES);

  it('renders both the CSV zone and the image/OCR zone', () => {
    expect(html).toContain('id="import-drop-zone"');
    expect(html).toContain('id="import-ocr-zone"');
  });

  it('CSV zone accepts spreadsheet/text file types', () => {
    expect(html).toContain('id="import-file-input"');
    expect(html).toContain('accept=".csv,.tsv,.txt,.xls"');
  });

  it('image zone accepts images + pdf and has a scan button', () => {
    expect(html).toContain('id="import-ocr-input"');
    expect(html).toContain('accept=".png,.jpg,.jpeg,.pdf"');
    expect(html).toContain('id="import-scan-btn"');
  });

  it('image zone has the template <select> with generic selected by default', () => {
    expect(html).toContain('id="import-ocr-template"');
    // generic option carries the selected attribute
    expect(html).toMatch(/<option value="generic" selected>/);
    // no other option is selected
    const selectedCount = (html.match(/ selected>/g) || []).length;
    expect(selectedCount).toBe(1);
  });

  it('preserves a passed-in OCR template selection (no reset to generic)', () => {
    const dk = renderDropZone(PO_TEMPLATES, 'digikey');
    expect(dk).toMatch(/<option value="digikey" selected>/);
    expect(dk).not.toMatch(/<option value="generic" selected>/);
    expect((dk.match(/ selected>/g) || []).length).toBe(1);
  });

  it('falls back to generic for an unknown/blank selection', () => {
    expect(renderDropZone(PO_TEMPLATES, 'bogus')).toMatch(/<option value="generic" selected>/);
    expect(renderDropZone(PO_TEMPLATES, '')).toMatch(/<option value="generic" selected>/);
  });

  it('offers the "+ add row manually" entry', () => {
    expect(html).toContain('id="import-add-row"');
  });

  it('renders blank-PO template buttons for every template', () => {
    Object.keys(PO_TEMPLATES).forEach(key => {
      expect(html).toContain(`data-template="${key}"`);
    });
  });

  it('has NO ★ Direct button (data-template="direct") and NO new-po-btn-direct class', () => {
    expect(html).not.toContain('data-template="direct"');
    expect(html).not.toContain('new-po-btn-direct');
  });
});

describe('renderOcrEngineNotice — missing-engine notice', () => {
  const html = renderOcrEngineNotice();

  it('contains the Install Tesseract button', () => {
    expect(html).toContain('id="install-tesseract-btn"');
    expect(html).toContain('Install Tesseract');
  });

  it('explains why the engine is needed', () => {
    expect(html).toContain('Tesseract OCR engine');
  });

  it('includes the copyable winget command fallback', () => {
    expect(TESSERACT_WINGET_COMMAND).toContain('UB-Mannheim.TesseractOCR');
    expect(html).toContain('<code>');
    expect(html).toContain(TESSERACT_WINGET_COMMAND);
  });

  it('wraps the notice in an identifiable container', () => {
    expect(html).toContain('id="ocr-engine-missing"');
    expect(html).toContain('class="ocr-engine-missing"');
  });
});
