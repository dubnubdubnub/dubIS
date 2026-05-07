/* mfg-direct-logic.js — Pure helpers for Direct import flow. */

const URL_RE = /^(https?:\/\/)?[a-z0-9-]+(\.[a-z0-9-]+)+(\/.*)?$/i;

export function looksLikeUrl(text) {
  return URL_RE.test((text || '').trim());
}

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
           match: { status: 'pending' } };
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
