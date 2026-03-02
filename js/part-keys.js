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
