/* part-keys.js — Canonical key derivation for BOM and inventory parts.
   Depends on: extractLCSC (from csv-parser.js). */

function bomKey(bom) {
  return (bom.lcsc || bom.mpn || "").toUpperCase();
}

function bomAggKey(bom) {
  return bomKey(bom) + (bom.dnp ? ":DNP" : "");
}

function invPartKey(item) {
  return item.lcsc || item.mpn || item.digikey || "";
}

function isDnp(val) {
  const v = (val || "").trim().toLowerCase();
  return v === "dnp" || v === "1" || v === "yes" || v === "true";
}

function extractPartIds(row, cols) {
  let lcsc = cols.lcsc !== -1 ? (row[cols.lcsc] || "").trim() : "";
  let mpn  = cols.mpn  !== -1 ? (row[cols.mpn]  || "").trim() : "";
  if (!lcsc && mpn) {
    const extracted = extractLCSC(mpn);
    if (extracted) lcsc = extracted;
  }
  return { lcsc: lcsc ? lcsc.toUpperCase() : "", mpn };
}

function rawRowAggKey(row, cols) {
  const { lcsc, mpn } = extractPartIds(row, cols);
  if (!lcsc && !mpn) return "";
  const base = lcsc || mpn.toUpperCase();
  return (cols.dnp !== -1 && isDnp(row[cols.dnp])) ? base + ":DNP" : base;
}
