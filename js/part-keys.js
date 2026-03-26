/* part-keys.js — Canonical key derivation for BOM and inventory parts.
   Depends on: isDnp, extractPartIds (from csv-parser.js). */

import { extractPartIds, isDnp } from './csv-parser.js';
import { escHtml } from './ui-helpers.js';

// ── Status display constants (shared by bom-panel.js and inventory-panel.js) ──
export const STATUS_ICONS = {
  ok: "+", short: "~", possible: "?", missing: "\u2014",
  manual: "\u2726", confirmed: "\u2714",
  "manual-short": "\u2726~", "confirmed-short": "\u2714~", dnp: "\u2716",
};
export const STATUS_ROW_CLASS = {
  ok: "row-green", short: "row-yellow", possible: "row-orange", missing: "row-red",
  manual: "row-pink", confirmed: "row-teal",
  "manual-short": "row-pink-short", "confirmed-short": "row-teal-short", dnp: "row-dnp",
};

// ── Shared status counting (used by bom-panel.js and inventory-panel.js) ──
export function countStatuses(rows) {
  const c = { ok: 0, short: 0, possible: 0, missing: 0, manual: 0, confirmed: 0, covered: 0, dnp: 0 };
  rows.forEach(r => {
    const st = r.effectiveStatus;
    if (st === "ok") c.ok++;
    else if (st === "short" || st === "manual-short" || st === "confirmed-short") c.short++;
    else if (st === "possible") c.possible++;
    else if (st === "missing") c.missing++;
    else if (st === "dnp") c.dnp++;
    if (st === "manual" || st === "manual-short") c.manual++;
    if (st === "confirmed" || st === "confirmed-short") c.confirmed++;
    if (r.coveredByAlts) c.covered++;
  });
  c.total = rows.length;
  return c;
}

export function bomKey(bom) {
  return (bom.lcsc || bom.mpn || "").toUpperCase();
}

export function bomAggKey(bom) {
  return bomKey(bom) + (bom.dnp ? ":DNP" : "");
}

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

export function colorizeRefs(refsStr) {
  if (!refsStr) return "";
  return refsStr.split(/,\s*/).map(function (ref) {
    var cls = refColorClass(ref);
    var escaped = escHtml(ref);
    return cls
      ? '<span class="' + cls + '" data-ref="' + escaped + '">' + escaped + '</span>'
      : '<span data-ref="' + escaped + '">' + escaped + '</span>';
  }).join(", ");
}
