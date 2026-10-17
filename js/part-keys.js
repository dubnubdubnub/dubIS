// @ts-check
/* part-keys.js — Canonical key derivation for BOM and inventory parts.
   Depends on: isDnp, extractPartIds (from csv-parser.js), the status
   registry (bom-status.js). */

import { extractPartIds, isDnp } from './csv-parser.js';
import { escHtml } from './ui-helpers.js';
import { BOM_STATUSES, BOM_STATUS_IDS, BOM_BUCKETS, statusSpec } from './bom-status.js';

// ── Status display constants (shared by bom-panel.js and inventory-panel.js) ──
// Derived from the status registry (js/bom-status.js); kept as lookup tables
// because that is the shape every renderer already indexes.
/** @type {Record<string, string>} */
export const STATUS_ICONS = Object.fromEntries(BOM_STATUS_IDS.map(id => [id, BOM_STATUSES[id].icon]));
/** @type {Record<string, string>} */
export const STATUS_ROW_CLASS = Object.fromEntries(BOM_STATUS_IDS.map(id => [id, BOM_STATUSES[id].rowClass]));

// ── Shared status counting (used by bom-panel.js and inventory-panel.js) ──
/**
 * Tallies effective statuses across BOM/inventory-BOM rows.
 *
 * Each row increments the counters its status's `buckets` name in the
 * registry (js/bom-status.js) — so `manual-short` counts as both short and
 * manual — plus `covered` when alternates cover a shortfall.
 *
 * `total` is every unique row, DNP and board-only included — that is what the
 * inventory filter bar's "All (N)" button filters over. `utbp` ("unique to be
 * placed") is `total` minus the rows whose status is declared not `placed`
 * (DNP, board-only): the count of unique parts that actually have to be placed
 * on the board. A row with an undeclared status still counts as placed — it
 * may well occupy a board position, so it must not silently drop out of UTBP.
 *
 * @param {Array<{ effectiveStatus?: string, coveredByAlts?: boolean }>} rows
 * @returns {{ ok: number, short: number, possible: number, missing: number,
 *   manual: number, confirmed: number, generic: number, covered: number,
 *   dnp: number, boardOnly: number, total: number, utbp: number }}
 */
export function countStatuses(rows) {
  /** @type {any} */
  const c = { covered: 0, total: 0, utbp: 0 };
  BOM_BUCKETS.forEach(b => { c[b.id] = 0; });
  let unplaced = 0;
  rows.forEach(r => {
    const spec = statusSpec(r.effectiveStatus);
    if (spec) {
      spec.buckets.forEach(b => { c[b]++; });
      if (!spec.placed) unplaced++;
    }
    if (r.coveredByAlts) c.covered++;
  });
  c.total = rows.length;
  c.utbp = rows.length - unplaced;
  return c;
}

export function bomKey(bom) {
  return (bom.lcsc || bom.mpn || "").toUpperCase();
}

export function bomAggKey(bom) {
  return bomKey(bom) + (bom.dnp ? ":DNP" : "");
}

/**
 * @param {import('./types.js').InventoryItem} item
 * @returns {string}
 */
export function invPartKey(item) {
  var lcsc = item.lcsc || "";
  if (lcsc && /^C/i.test(lcsc)) return lcsc;
  return item.mpn || item.digikey || item.pololu || item.mouser || "";
}

export function rawRowAggKey(row, cols) {
  const { lcsc, mpn } = extractPartIds(row, cols);
  if (!lcsc && !mpn) return "";
  const base = lcsc || mpn.toUpperCase();
  return (cols.dnp !== -1 && isDnp(row[cols.dnp])) ? base + ":DNP" : base;
}

// ── Designator color coding (shared by bom-panel.js and inventory-panel.js) ──

export const REF_COLOR_MAP = {
  R: "ref-r", RM: "ref-r",
  C: "ref-c",
  Y: "ref-osc", X: "ref-osc",
  U: "ref-ic", IC: "ref-ic", Q: "ref-ic",
  L: "ref-l",
  D: "ref-d", LED: "ref-d",
};

export function refColorClass(ref) {
  const m = ref.trim().match(/^([A-Za-z]+)/);
  if (!m) return "";
  return REF_COLOR_MAP[m[1].toUpperCase()] || "";
}

export function compressRefs(refsStr) {
  if (!refsStr) return "";
  var refs = refsStr.split(/,\s*/);
  if (refs.length <= 1) return refsStr.trim();

  // Parse each ref into { prefix, num, raw }
  var parsed = refs.map(function (r) {
    var trimmed = r.trim();
    var m = trimmed.match(/^([A-Za-z]+)(\d+)$/);
    return m ? { prefix: m[1].toUpperCase(), num: parseInt(m[2], 10), raw: trimmed, origPrefix: m[1] } : { prefix: null, num: null, raw: trimmed, origPrefix: null };
  });

  // Group consecutive runs with same prefix
  var ranges = [];
  var i = 0;
  while (i < parsed.length) {
    var start = parsed[i];
    if (start.prefix === null) {
      ranges.push(start.raw);
      i++;
      continue;
    }
    var end = start;
    while (i + 1 < parsed.length && parsed[i + 1].prefix === start.prefix && parsed[i + 1].num === end.num + 1) {
      end = parsed[++i];
    }
    if (start === end) {
      ranges.push(start.raw);
    } else {
      ranges.push(start.origPrefix + start.num + "\u2013" + end.origPrefix + end.num);
    }
    i++;
  }

  return ranges.join(", ");
}

export function colorizeRefs(refsStr) {
  if (!refsStr) return "";
  var refs = refsStr.split(/,\s*/);
  if (refs.length === 0) return "";

  // Parse each ref into { prefix, num, raw }
  var parsed = refs.map(function (r) {
    var trimmed = r.trim();
    var m = trimmed.match(/^([A-Za-z]+)(\d+)$/);
    return m ? { prefix: m[1].toUpperCase(), num: parseInt(m[2], 10), raw: trimmed, origPrefix: m[1] } : { prefix: null, num: null, raw: trimmed, origPrefix: null };
  });

  // Build ranges and colorize
  var parts = [];
  var i = 0;
  while (i < parsed.length) {
    var start = parsed[i];
    if (start.prefix === null) {
      var escaped = escHtml(start.raw);
      parts.push('<span data-ref="' + escaped + '">' + escaped + '</span>');
      i++;
      continue;
    }
    var end = start;
    while (i + 1 < parsed.length && parsed[i + 1].prefix === start.prefix && parsed[i + 1].num === end.num + 1) {
      end = parsed[++i];
    }
    var cls = refColorClass(start.raw);
    if (start === end) {
      // Single ref — use data-ref (unchanged from before)
      var esc = escHtml(start.raw);
      parts.push(cls
        ? '<span class="' + cls + '" data-ref="' + esc + '">' + esc + '</span>'
        : '<span data-ref="' + esc + '">' + esc + '</span>');
    } else {
      // Range — use data-refs with space-separated individual ref names
      // (HTML doesn't allow duplicate attributes, so we use a single data-refs)
      var label = escHtml(start.origPrefix + start.num + "\u2013" + end.num);
      var allRefs = [];
      for (var n = start.num; n <= end.num; n++) {
        allRefs.push(escHtml(start.origPrefix + n));
      }
      var dataRefsAttr = ' data-refs="' + allRefs.join(' ') + '"';
      parts.push(cls
        ? '<span class="' + cls + '"' + dataRefsAttr + '>' + label + '</span>'
        : '<span' + dataRefsAttr + '>' + label + '</span>');
    }
    i++;
  }

  return parts.join(", ");
}
