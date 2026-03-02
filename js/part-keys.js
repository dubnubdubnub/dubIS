/* part-keys.js — Canonical key derivation for BOM and inventory parts.
   Depends on: isDnp, extractPartIds (from csv-parser.js). */

// ── Status display constants (shared by bom-panel.js and inventory-panel.js) ──
const STATUS_ICONS = {
  ok: "+", short: "~", possible: "?", missing: "\u2014",
  manual: "\u2726", confirmed: "\u2714",
  "manual-short": "\u2726~", "confirmed-short": "\u2714~", dnp: "\u2716",
};
const STATUS_ROW_CLASS = {
  ok: "row-green", short: "row-yellow", possible: "row-orange", missing: "row-red",
  manual: "row-pink", confirmed: "row-teal",
  "manual-short": "row-pink-short", "confirmed-short": "row-teal-short", dnp: "row-dnp",
};

// ── Shared status counting (used by bom-panel.js and inventory-panel.js) ──
function countStatuses(rows) {
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

function bomKey(bom) {
  return (bom.lcsc || bom.mpn || "").toUpperCase();
}

function bomAggKey(bom) {
  return bomKey(bom) + (bom.dnp ? ":DNP" : "");
}

function invPartKey(item) {
  return item.lcsc || item.mpn || item.digikey || "";
}

function rawRowAggKey(row, cols) {
  const { lcsc, mpn } = extractPartIds(row, cols);
  if (!lcsc && !mpn) return "";
  const base = lcsc || mpn.toUpperCase();
  return (cols.dnp !== -1 && isDnp(row[cols.dnp])) ? base + ":DNP" : base;
}
