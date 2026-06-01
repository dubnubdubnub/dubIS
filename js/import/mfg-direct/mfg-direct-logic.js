/* mfg-direct-logic.js — Pure helpers for Direct import flow. */

export function canonicalizeUrl(text) {
  let s = (text || '').trim();
  if (!s) return '';
  if (!/^https?:\/\//i.test(s)) s = 'https://' + s;
  try {
    const u = new URL(s);
    return u.origin + u.pathname.replace(/\/$/, '');
  } catch (err) {
    void err;
    return '';
  }
}

export function emptyLineItem() {
  return { mpn: '', manufacturer: '', package: '', quantity: 0, unit_price: 0,
           distributor: '', distributor_pn: '', match: { status: 'pending' } };
}

/**
 * Map a backend scan payload's line items into the editor's line-item shape.
 * Pure (no DOM, no api) so it can be unit-tested. Each item gets a fresh
 * pending match status; distributor/distributor_pn are preserved so they flow
 * into the PO import (backend reads them to populate the right ledger column).
 * @param {Array<Object>} lineItems - payload.line_items
 * @param {string} [template] - template the items were parsed under
 * @returns {Array<Object>}
 */
export function mapScanLineItems(lineItems, template = 'generic') {
  return (lineItems || []).map(p => ({
    mpn: p.mpn || '',
    manufacturer: p.manufacturer || '',
    package: p.package || '',
    quantity: p.quantity || 0,
    unit_price: p.unit_price || 0,
    distributor: p.distributor || template || '',
    distributor_pn: p.distributor_pn || '',
    match: { status: 'pending' },
  }));
}

/**
 * Build the sourceFile object from a scan payload so the existing import path
 * persists the uploaded photo as the PO source file. Pure/testable.
 * @param {{image_b64?: string, filename?: string}} payload
 * @returns {{name: string, bytes: string} | null}
 */
export function scanSourceFile(payload) {
  if (!payload || !payload.image_b64) return null;
  return { name: payload.filename || 'scan.jpg', bytes: payload.image_b64 };
}

export function validateLineItems(items) {
  const errors = [];
  items.forEach((li, idx) => {
    if (!(li.mpn || '').trim()) errors.push({ idx, msg: 'MPN required' });
    if (!li.quantity || li.quantity <= 0) errors.push({ idx, msg: 'qty must be > 0' });
  });
  return errors;
}

export function formatMatchBadge(match) {
  if (!match || match.status === 'new')      return { label: '+ new', cls: 'match-new' };
  if (match.status === 'definite')           return { label: '✓', cls: 'match-definite' };
  if (match.status === 'possible') {
    const top = (match.candidates && match.candidates[0]) || {};
    return { label: `~ ${top.mpn || '?'} ?`, cls: 'match-possible' };
  }
  if (match.status === 'pending')            return { label: '…', cls: 'match-pending' };
  return { label: '', cls: '' };
}
