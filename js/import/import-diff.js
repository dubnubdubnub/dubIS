// @ts-check
/**
 * js/import/import-diff.js — Pure import diff computation.
 *
 * computeImportDiff(invRows, inventory)
 *   → DiffEntry[]
 *
 * DiffEntry: { row, partKey, status, currentQty, addQty, resultingQty, matchedItem, skipReason? }
 *
 * Pure: no globals, no DOM, no imports from store/api. Pass inventory in.
 * Reusable from CSV import and OCR import paths.
 */

/**
 * Derive a part key from an invRow (transformed import row with full field names).
 * Mirrors the logic of invPartKey() in part-keys.js but uses the long field names
 * that transformImportRows() produces.
 *
 * @param {Object} invRow
 * @returns {string}
 */
function invRowKey(invRow) {
  const lcsc = (invRow['LCSC Part Number'] || '').trim();
  if (lcsc && /^C/i.test(lcsc)) return lcsc;
  return (
    (invRow['Manufacture Part Number'] || '').trim() ||
    (invRow['Digikey Part Number'] || '').trim() ||
    (invRow['Pololu Part Number'] || '').trim() ||
    (invRow['Mouser Part Number'] || '').trim() ||
    ''
  );
}

/**
 * Derive a part key from an inventory item (short field names).
 * Mirrors invPartKey() in part-keys.js.
 *
 * @param {Object} item
 * @returns {string}
 */
function itemKey(item) {
  const lcsc = (item.lcsc || '').trim();
  if (lcsc && /^C/i.test(lcsc)) return lcsc;
  return (
    (item.mpn || '').trim() ||
    (item.digikey || '').trim() ||
    (item.pololu || '').trim() ||
    (item.mouser || '').trim() ||
    ''
  );
}

/**
 * @typedef {{
 *   row: Object,
 *   partKey: string,
 *   status: 'insert'|'update'|'skip',
 *   currentQty: number,
 *   addQty: number,
 *   resultingQty: number,
 *   matchedItem: Object|null,
 *   skipReason?: string
 * }} DiffEntry
 */

/**
 * Compute the import diff between proposed invRows and the current inventory.
 *
 * Multiple invRows with the same key are merged: their addQty values sum
 * (same behaviour as the backend merge).
 *
 * @param {Object[]} invRows — rows from transformImportRows()
 * @param {Object[]} inventory — current inventory items (InventoryItem[])
 * @returns {DiffEntry[]}
 */
export function computeImportDiff(invRows, inventory) {
  // Build a lookup map from part key → inventory item.
  /** @type {Map<string, Object>} */
  const invMap = new Map();
  for (const item of inventory) {
    const k = itemKey(item);
    if (k) invMap.set(k, item);
  }

  // Accumulate rows by key so duplicate keys in the import file sum up.
  /** @type {Map<string, DiffEntry>} */
  const seen = new Map();

  /** @type {DiffEntry[]} */
  const result = [];

  for (const row of invRows) {
    const key = invRowKey(row);

    // Parse qty — treat empty/invalid as 0
    const rawQty = (row['Quantity'] || '').toString().replace(/,/g, '').trim();
    const qty = parseInt(rawQty, 10);

    if (!key) {
      result.push({
        row,
        partKey: '',
        status: 'skip',
        currentQty: 0,
        addQty: 0,
        resultingQty: 0,
        matchedItem: null,
        skipReason: 'no part identifier',
      });
      continue;
    }

    if (isNaN(qty) || qty <= 0) {
      result.push({
        row,
        partKey: key,
        status: 'skip',
        currentQty: 0,
        addQty: qty || 0,
        resultingQty: 0,
        matchedItem: null,
        skipReason: 'qty must be > 0',
      });
      continue;
    }

    if (seen.has(key)) {
      // Accumulate into the existing entry
      const entry = seen.get(key);
      entry.addQty += qty;
      entry.resultingQty = entry.currentQty + entry.addQty;
      continue;
    }

    const matchedItem = invMap.get(key) || null;
    const currentQty = matchedItem ? (matchedItem.qty || 0) : 0;
    /** @type {DiffEntry} */
    const entry = {
      row,
      partKey: key,
      status: /** @type {'update'|'insert'} */ (matchedItem ? 'update' : 'insert'),
      currentQty,
      addQty: qty,
      resultingQty: currentQty + qty,
      matchedItem,
    };
    seen.set(key, entry);
    result.push(entry);
  }

  return result;
}
