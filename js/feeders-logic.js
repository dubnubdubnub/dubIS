/* feeders-logic.js — Pure helpers for the Feeders loading-station UI.
   No DOM access here — kept separate from feeders-modal.js so it's
   unit-testable without a browser environment. */

import { invPartKey } from './part-keys.js';

/**
 * Describe a feeder's loaded reel for display: the canonical part_key plus
 * a resolved description when the part is still findable in inventory
 * (it might not be — e.g. the part was deleted after the reel was loaded).
 *
 * @param {{part_key:string, qty:number, tape_width_mm:number|null}|null} loaded
 * @param {Array<object>} inventory
 * @returns {{part_key:string, description:string, resolved:boolean}|null}
 */
export function describeLoadedPart(loaded, inventory) {
  if (!loaded) return null;
  const item = (inventory || []).find((i) => invPartKey(i) === loaded.part_key);
  return {
    part_key: loaded.part_key,
    description: item ? (item.description || '') : '',
    resolved: !!item,
  };
}

/**
 * Substring search over inventory by part key / MPN / description.
 * Mirrors the resolution fields the loading-station operator would recognize
 * a reel by. Returns at most `limit` matches, inventory order preserved.
 *
 * @param {Array<object>} inventory
 * @param {string} term
 * @param {number} [limit]
 * @returns {Array<object>}
 */
export function searchParts(inventory, term, limit = 8) {
  const t = (term || '').trim().toLowerCase();
  if (!t) return [];
  const out = [];
  for (const item of inventory || []) {
    const pk = invPartKey(item).toLowerCase();
    const desc = (item.description || '').toLowerCase();
    const mpn = (item.mpn || '').toLowerCase();
    if (pk.includes(t) || desc.includes(t) || mpn.includes(t)) {
      out.push(item);
      if (out.length >= limit) break;
    }
  }
  return out;
}

/**
 * Validate the "Register feeder" form.
 * @param {{tag_id?:string, feeder_type?:string}} values
 * @returns {Record<string,string>|null}
 */
export function validateRegisterForm(values) {
  const errors = {};
  const tagId = (values.tag_id || '').trim();
  if (!tagId) errors.tag_id = 'Tag id required';
  else if (!/^\d+$/.test(tagId)) errors.tag_id = 'Tag id must be a non-negative integer (AprilTag id)';

  if (!(values.feeder_type || '').trim()) errors.feeder_type = 'Feeder type required';

  return Object.keys(errors).length ? errors : null;
}

/**
 * Validate the "Load feeder" form.
 * @param {{part_key?:string, qty?:string, tape_width_mm?:string}} values
 * @returns {Record<string,string>|null}
 */
export function validateLoadForm(values) {
  const errors = {};
  if (!(values.part_key || '').trim()) errors.part_key = 'Pick a part';

  const qtyStr = (values.qty ?? '').toString().trim();
  const qty = Number(qtyStr);
  if (qtyStr === '' || !Number.isFinite(qty) || qty < 0 || !Number.isInteger(qty)) {
    errors.qty = 'Enter a non-negative integer quantity';
  }

  const twStr = (values.tape_width_mm ?? '').toString().trim();
  if (twStr !== '') {
    const tw = Number(twStr);
    if (!Number.isFinite(tw) || tw <= 0) errors.tape_width_mm = 'Enter a positive number, or leave blank for auto';
  }

  return Object.keys(errors).length ? errors : null;
}

/**
 * Validate the "Download tag sheet" form.
 * @param {{start?:string, count?:string}} values
 * @returns {Record<string,string>|null}
 */
export function validateSheetForm(values) {
  const errors = {};
  const startStr = (values.start ?? '').toString().trim();
  const start = Number(startStr);
  if (startStr === '' || !Number.isFinite(start) || start < 0 || !Number.isInteger(start)) {
    errors.start = 'Enter a non-negative integer';
  }

  const countStr = (values.count ?? '').toString().trim();
  const count = Number(countStr);
  if (countStr === '' || !Number.isFinite(count) || count < 1 || !Number.isInteger(count)) {
    errors.count = 'Enter an integer >= 1';
  }

  return Object.keys(errors).length ? errors : null;
}

/**
 * Format the "Tape (mm)" grid cell.
 * @param {{tape_width_mm:number|null}|null} loaded
 * @returns {string}
 */
export function formatTapeWidth(loaded) {
  if (!loaded || loaded.tape_width_mm === null || loaded.tape_width_mm === undefined) return '—';
  return String(loaded.tape_width_mm);
}

/**
 * Format the "Qty" grid cell.
 * @param {{qty:number}|null} loaded
 * @returns {string}
 */
export function formatLoadedQty(loaded) {
  if (!loaded) return '—';
  return String(loaded.qty);
}
