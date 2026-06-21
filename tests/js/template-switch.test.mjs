// tests/js/template-switch.test.mjs
import { describe, it, expect } from 'vitest';
import { templateVendorName, reparseRowsForTemplate } from '../../js/import/mfg-direct/template-switch.js';

describe('templateVendorName', () => {
  it('maps distributor templates to vendor names', () => {
    expect(templateVendorName('lcsc')).toBe('LCSC');
    expect(templateVendorName('digikey')).toBe('DigiKey');
    expect(templateVendorName('mouser')).toBe('Mouser');
    expect(templateVendorName('pololu')).toBe('Pololu');
    expect(templateVendorName('generic')).toBeNull();
  });
});

describe('reparseRowsForTemplate', () => {
  it('sets distributor to the template name for distributor templates', () => {
    const rows = [{ mpn: 'X', distributor: 'generic', distributor_pn: '' }];
    const out = reparseRowsForTemplate(rows, 'lcsc');
    expect(out[0].distributor).toBe('lcsc');
  });

  it('clears distributor for generic', () => {
    const rows = [{ mpn: 'X', distributor: 'lcsc', distributor_pn: 'C1' }];
    const out = reparseRowsForTemplate(rows, 'generic');
    expect(out[0].distributor).toBe('generic');
    expect(out[0].distributor_pn).toBe('');
  });
});
