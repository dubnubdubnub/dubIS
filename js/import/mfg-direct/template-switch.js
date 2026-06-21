/* template-switch.js — pure functions for switching the OCR overlay template
   after upload. Re-routes distributor columns over already-extracted rows without
   re-OCR, and resolves the matching vendor name for auto-prefill. No DOM, no API,
   no store imports — pure data transforms only. */

const _VENDOR = { lcsc: 'LCSC', digikey: 'DigiKey', mouser: 'Mouser', pololu: 'Pololu' };
const _DIST = new Set(['lcsc', 'digikey', 'mouser', 'pololu']);

/** Map a distributor template key to the canonical vendor name, or null for generic. */
export function templateVendorName(template) {
  return _VENDOR[(template || '').toLowerCase()] || null;
}

/**
 * Re-route distributor/distributor_pn columns for a new template over already-
 * extracted rows (no re-OCR). Returns a new array of row objects.
 * - Distributor templates: set `distributor` to the template name (keeps distributor_pn).
 * - Generic: set `distributor` to 'generic', clear `distributor_pn`.
 */
export function reparseRowsForTemplate(rows, template) {
  const t = (template || 'generic').toLowerCase();
  const isDist = _DIST.has(t);
  return (rows || []).map(r => {
    const next = { ...r };
    next.distributor = isDist ? t : 'generic';
    if (!isDist) next.distributor_pn = '';
    return next;
  });
}
