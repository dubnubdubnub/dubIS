/**
 * Load real data fixtures for integration tests.
 * Parses inventory.csv into the same shape the Python backend produces,
 * and provides raw BOM text for processBOM().
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(__dirname, '..', '..', 'fixtures');

function readFixture(name) {
  return readFileSync(join(FIXTURES, name), 'utf-8');
}

function readFixtureBytes(name) {
  return readFileSync(join(FIXTURES, name));
}

function readFixtureJSON(name) {
  return JSON.parse(readFixture(name));
}

/**
 * Parse inventory.csv into the same object shape as _load_organized() in Python.
 * Skips section separator rows (=== ... ===) and rows without LCSC or MPN.
 */
function loadInventory() {
  const text = readFixture('inventory.csv');
  const lines = text.split(/\r?\n/);
  const headers = lines[0].split(',');

  // Find column indices
  const colIdx = {};
  headers.forEach((h, i) => { colIdx[h.trim()] = i; });

  const items = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;

    // RFC 4180 light parse (handle quoted fields with commas)
    const fields = [];
    let field = '';
    let inQuotes = false;
    for (let j = 0; j < line.length; j++) {
      const c = line[j];
      if (inQuotes) {
        if (c === '"') {
          if (j + 1 < line.length && line[j + 1] === '"') { field += '"'; j++; }
          else inQuotes = false;
        } else { field += c; }
      } else {
        if (c === '"') inQuotes = true;
        else if (c === ',') { fields.push(field.trim()); field = ''; }
        else field += c;
      }
    }
    fields.push(field.trim());

    const section = fields[colIdx['Section']] || '';
    const lcsc = fields[colIdx['LCSC Part Number']] || '';
    const mpn = fields[colIdx['Manufacture Part Number']] || '';

    if (section.startsWith('=') || lcsc.startsWith('=')) continue;
    if (!lcsc && !mpn) continue;

    const qtyStr = (fields[colIdx['Quantity']] || '0').replace(/,/g, '');
    const qty = parseInt(qtyStr, 10) || 0;
    const unitStr = (fields[colIdx['Unit Price($)']] || '0').replace(/,/g, '') || '0';
    const extStr = (fields[colIdx['Ext.Price($)']] || '0').replace(/,/g, '') || '0';

    items.push({
      section,
      lcsc,
      mpn,
      digikey: fields[colIdx['Digikey Part Number']] || '',
      manufacturer: fields[colIdx['Manufacturer']] || '',
      package: fields[colIdx['Package']] || '',
      description: fields[colIdx['Description']] || '',
      qty,
      unit_price: parseFloat(unitStr) || 0,
      ext_price: parseFloat(extStr) || 0,
    });
  }
  return items;
}

export { readFixture, readFixtureBytes, readFixtureJSON, loadInventory, FIXTURES };
