/* ocr-overlay-highlight.js — highlight boxes for a prefill row, attributed to
   the model that produced it.

   VLM rows carry their own pixel bbox; grid/flat rows (Tesseract) are matched to
   the Tesseract word tokens whose text appears in the row's fields.

   Pure module: no DOM, no api, no store imports. */

const _LABELS = { vlm: 'VLM', grid: 'OCR grid', flat: 'OCR' };

export function backendLabel(backend) { return _LABELS[backend] || ''; }

function _norm(s) { return String(s === null || s === undefined ? '' : s).toLowerCase().replace(/[^a-z0-9]/g, ''); }

export function rowHighlightBoxes(row, page) {
  if (!row || !page) return [];
  const bb = row.bbox;
  if (Array.isArray(bb) && bb.length === 4 && bb.every(n => typeof n === 'number')) {
    return [{ x: bb[0], y: bb[1], w: bb[2], h: bb[3] }];
  }
  // Fuzzy match: collect normalized text from the row's value fields, then keep
  // any word token whose normalized text is a substring (length ≥ 2) of a field.
  const fieldVals = ['distributor_pn', 'mpn', 'manufacturer', 'description',
    'package', 'quantity', 'unit_price']
    .map(k => _norm(row[k])).filter(v => v.length >= 2);
  if (!fieldVals.length) return [];
  const boxes = [];
  for (const w of (page.words || [])) {
    const wt = _norm(w.text);
    if (wt.length < 2) continue;
    if (fieldVals.some(v => v.includes(wt) || wt.includes(v))) {
      boxes.push({ x: w.x, y: w.y, w: w.w, h: w.h });
    }
  }
  return boxes;
}
