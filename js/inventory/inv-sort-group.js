// @ts-check
/* inv-sort-group.js — Pure helpers for the inventory column-header
   scope-cycling sort and vendor-grouping. No DOM, no store, no events. */

/**
 * Advance the scope cycle for a column based on the current Group level.
 * Returns the next scope string, or null to mean "off / default".
 *
 * groupLevel=0 (full hierarchy): subsection → section → global → null
 * groupLevel=1 (sections only):           section → global → null
 * groupLevel=2 (flat):                              global → null
 *
 * If the current scope is not reachable at the given level (e.g. user
 * lowered the Group level while a finer-grained scope was active),
 * coerces forward to the first reachable scope.
 *
 * @param {number} groupLevel  0 | 1 | 2
 * @param {string|null} current  null | "subsection" | "section" | "global"
 * @returns {string|null}
 */
export function nextScope(groupLevel, current) {
  var cycle;
  if (groupLevel === 0) cycle = ['subsection', 'section', 'global'];
  else if (groupLevel === 1) cycle = ['section', 'global'];
  else cycle = ['global'];

  if (current === null) return cycle[0];

  var idx = cycle.indexOf(current);
  if (idx === -1) return cycle[0];
  if (idx === cycle.length - 1) return null;
  return cycle[idx + 1];
}

/**
 * Comparator config: { field, type, dir }
 *   type: "num" | "str"
 *   dir: 1 (asc) | -1 (desc)
 *   field: property accessor; "value" is computed (qty * unit_price)
 */
var SORT_CONFIG = {
  mpn:         { field: 'mpn',         type: 'str', dir: 1 },
  description: { field: 'description', type: 'str', dir: 1 },
  qty:         { field: 'qty',         type: 'num', dir: -1 },
  unit_price:  { field: 'unit_price',  type: 'num', dir: -1 },
  value:       { field: '__value',     type: 'num', dir: -1 },
};

function getNumeric(item, field) {
  if (field === '__value') {
    return (Number(item.qty) || 0) * (Number(item.unit_price) || 0);
  }
  return Number(item[field]) || 0;
}

function getString(item, field) {
  return String(item[field] || '');
}

/**
 * Return a new array of parts sorted by the given column.
 * Each column has a fixed natural direction (numeric=desc, string=asc).
 * @param {Array<Object>} parts
 * @param {string|null} column  null | "mpn" | "description" | "qty" | "unit_price" | "value"
 * @returns {Array<Object>}
 */
export function sortPartsBy(parts, column) {
  if (column === null || !SORT_CONFIG[column]) return parts;
  var cfg = SORT_CONFIG[column];
  var copy = parts.slice();
  if (cfg.type === 'num') {
    copy.sort(function (a, b) {
      var av = getNumeric(a, cfg.field);
      var bv = getNumeric(b, cfg.field);
      return (av - bv) * cfg.dir;
    });
  } else {
    copy.sort(function (a, b) {
      var av = getString(a, cfg.field);
      var bv = getString(b, cfg.field);
      // Empty strings sort last regardless of direction.
      if (av === '' && bv !== '') return 1;
      if (bv === '' && av !== '') return -1;
      var cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return cmp * cfg.dir;
    });
  }
  return copy;
}

var VENDOR_ORDER = ['lcsc', 'digikey', 'mouser', 'pololu', 'other'];

function classifyVendor(item) {
  if (item.lcsc) return 'lcsc';
  if (item.digikey) return 'digikey';
  if (item.mouser) return 'mouser';
  if (item.pololu) return 'pololu';
  return 'other';
}

/**
 * Split parts into vendor piles in canonical order.
 * Empty piles are omitted from the returned array.
 * Parts retain their original relative order within each pile.
 * @param {Array<Object>} parts
 * @returns {Array<{vendor: string, parts: Array<Object>}>}
 */
export function groupByVendor(parts) {
  var buckets = { lcsc: [], digikey: [], mouser: [], pololu: [], other: [] };
  for (var i = 0; i < parts.length; i++) {
    buckets[classifyVendor(parts[i])].push(parts[i]);
  }
  var out = [];
  for (var j = 0; j < VENDOR_ORDER.length; j++) {
    var v = VENDOR_ORDER[j];
    if (buckets[v].length > 0) out.push({ vendor: v, parts: buckets[v] });
  }
  return out;
}
