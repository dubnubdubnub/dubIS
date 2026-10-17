// @ts-check
/* csv-parser.js — RFC 4180-compliant CSV parser and column detection */

// ── Parse CSV (handles quoted fields with commas/newlines) ──
export function parseCSV(text) {
  // Auto-detect delimiter from first line
  const firstLine = text.split(/\r?\n/)[0];
  const tabCount = (firstLine.match(/\t/g) || []).length;
  const commaCount = (firstLine.match(/,/g) || []).length;
  const delim = tabCount > commaCount ? '\t' : ',';

  const lines = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (i + 1 < text.length && text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else {
      if (c === '"') {
        inQuotes = true;
      } else if (c === delim) {
        row.push(field.trim());
        field = "";
      } else if (c === '\n' || c === '\r') {
        if (c === '\r' && i + 1 < text.length && text[i + 1] === '\n') i++;
        row.push(field.trim());
        if (row.some(f => f !== "")) lines.push(row);
        row = [];
        field = "";
      } else {
        field += c;
      }
    }
  }
  row.push(field.trim());
  if (row.some(f => f !== "")) lines.push(row);
  return lines;
}

// ── Auto-detect BOM columns ──
// `lcsc` is the first LCSC-like column; `lcscCols` lists every one of them
// (e.g. KiCad BOMs carrying both `LCSC` and `LCSC ID`), read in header order.
//
// `dnp` and `exclude` are separate columns with separate meanings. DNP is a
// real part deliberately not placed; "Exclude from BOM" is the design saying
// there is nothing to buy at all (a fiducial, a logo) — the `board-only`
// status in js/bom-status.js. They used to share `dnp`, first match wins, so
// KiCad's default export (which carries both a DNP and an "Exclude from BOM"
// column) silently dropped the exclude flag. A BOM with ONLY an exclude
// column used to read it as DNP; it now reads it as board-only. Neither
// counts toward UTBP, missing, or the cart, so the numbers a user sees are
// unchanged — only the chip the row lands under, and the fact that an
// excluded row is no longer consumed (DNP rows no longer are either).
export function detectBOMColumns(headers) {
  const cols = { lcsc: -1, lcscCols: [], mpn: -1, qty: -1, ref: -1, desc: -1, value: -1, footprint: -1, dnp: -1, exclude: -1 };
  const lower = headers.map(h => h.toLowerCase());
  lower.forEach((h, i) => {
    if (/lcsc/.test(h) || /jlcpcb/.test(h) || /supplier.*part/.test(h) || (h.includes("part") && (h.includes("#") || h.includes("number")) && !h.includes("manufactur"))) cols.lcscCols.push(i);
    if (cols.ref === -1 && (/designator/.test(h) || /reference/.test(h))) cols.ref = i;
    if (cols.qty === -1 && (/^qty$/.test(h) || /quantity/.test(h))) cols.qty = i;
    if (cols.desc === -1 && (/description/.test(h) || /^desc$/.test(h) || /^comment$/.test(h))) cols.desc = i;
    if (cols.value === -1 && h === "value") cols.value = i;
    if (cols.mpn === -1 && (/^mpn$/.test(h) || /manufactur.*part/.test(h))) cols.mpn = i;
    if (cols.footprint === -1 && /footprint/.test(h)) cols.footprint = i;
    if (cols.dnp === -1 && (/^dnp$/.test(h) || /^procurement/.test(h))) cols.dnp = i;
    if (cols.exclude === -1 && /exclude.*bom/.test(h)) cols.exclude = i;
  });
  if (cols.lcscCols.length) cols.lcsc = cols.lcscCols[0];
  // If no explicit MPN column but there is a Value column, use Value as MPN fallback
  // (a blank MPN cell also falls back to Value per row, in extractPartIds)
  if (cols.mpn === -1 && cols.value !== -1) cols.mpn = cols.value;
  return cols;
}

// ── Extract LCSC part number from a value string ──
export function extractLCSC(s) {
  const m = s.match(/\b(C\d{4,})\b/i);
  return m ? m[1].toUpperCase() : null;
}

// ── Check if a DNP column value indicates "Do Not Place" ──
// The "excluded…" spellings stay: a JLC "Procurement Type" column (mapped to
// `dnp`) can carry them.
export function isDnp(val) {
  const v = (val || "").trim().toLowerCase();
  return v === "dnp" || v === "1" || v === "yes" || v === "true"
      || v === "excluded from bom" || v === "excluded" || v === "exclude";
}

// ── Check if an "Exclude from BOM" column value is set ──
// Same truthy vocabulary as isDnp — KiCad writes "Excluded from BOM", other
// exporters 1/yes/true — but a different question, so a different name.
export function isExcludedFromBom(val) {
  return isDnp(val);
}

// ── Extract LCSC + MPN from a raw BOM row using detected column indices ──
export function extractPartIds(row, cols) {
  // Hand-built cols (tests, older callers) may carry only `lcsc`.
  const lcscCols = cols.lcscCols || (cols.lcsc !== -1 ? [cols.lcsc] : []);
  let lcsc = "";
  for (const ci of lcscCols) {
    lcsc = (row[ci] || "").trim();
    if (lcsc) break;
  }
  let mpn = cols.mpn !== -1 ? (row[cols.mpn] || "").trim() : "";
  // KiCad exports an MPN column even when every cell is blank: use Value then
  if (!mpn && cols.value !== -1) mpn = (row[cols.value] || "").trim();
  if (!lcsc && mpn) {
    const extracted = extractLCSC(mpn);
    if (extracted) lcsc = extracted;
  }
  return { lcsc: lcsc ? lcsc.toUpperCase() : "", mpn };
}

// ── Generate RFC 4180 CSV from headers + rows ──
export function generateCSV(headers, rows) {
  function escapeField(val) {
    // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
    const s = val == null ? "" : String(val);
    if (s.indexOf(",") !== -1 || s.indexOf('"') !== -1 || s.indexOf("\n") !== -1) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }
  const lines = [headers.map(escapeField).join(",")];
  rows.forEach(row => {
    lines.push(row.map(escapeField).join(","));
  });
  return lines.join("\r\n") + "\r\n";
}

// ── Aggregate raw BOM rows into a Map keyed by part+DNP ──
export function aggregateBomRows(rawRows, headers, cols) {
  const aggregated = new Map();
  const warnings = [];

  rawRows.forEach((row, ri) => {
    const { lcsc, mpn } = extractPartIds(row, cols);

    const ref       = cols.ref       !== -1 ? (row[cols.ref]       || "").trim() : "";

    let qty;
    if (cols.qty !== -1) {
      const rawQty = parseInt(row[cols.qty], 10);
      if (isNaN(rawQty) || rawQty <= 0) {
        if ((row[cols.qty] || "").trim() !== "")
          warnings.push({ ri, msg: "Invalid qty '" + (row[cols.qty] || "") + "', defaulting to 1" });
        qty = 1;
      } else { qty = rawQty; }
    } else {
      // No Qty column: grouped BOMs (e.g. KiCad) imply quantity via the
      // designator list, so count the designators (comma/space separated).
      const designators = ref ? ref.split(/[,\s]+/).filter(Boolean).length : 0;
      qty = designators > 0 ? designators : 1;
    }

    const desc      = cols.desc      !== -1 ? (row[cols.desc]      || "").trim() : "";
    const value     = cols.value     !== -1 ? (row[cols.value]     || "").trim() : "";
    const footprint = cols.footprint !== -1 ? (row[cols.footprint] || "").trim() : "";
    const dnp = cols.dnp !== -1 && isDnp(row[cols.dnp]);
    // Hand-built cols (tests, older callers) may carry no `exclude`.
    const excludeCol = cols.exclude ?? -1;
    const excluded = excludeCol !== -1 && isExcludedFromBom(row[excludeCol]);

    const rawCols = {};
    headers.forEach((h, i) => { rawCols[h] = (row[i] || "").trim(); });

    if (!lcsc && !mpn) { warnings.push({ ri, msg: "No LCSC or MPN" }); return; }

    const key = (lcsc || mpn.toUpperCase()) + (dnp ? ":DNP" : "");
    if (aggregated.has(key)) {
      const existing = aggregated.get(key);
      existing.qty += qty;
      if (ref) { existing.refs = existing.refs ? existing.refs + ", " + ref : ref; }
      if (cols.qty !== -1) existing.rawCols[headers[cols.qty]] = String(existing.qty);
      if (cols.ref !== -1) existing.rawCols[headers[cols.ref]] = existing.refs;
      // Excluded only if EVERY merged row is: one instance that is not
      // excluded still has to be bought, so the line must stay a part.
      existing.excluded = existing.excluded && excluded;
    } else {
      aggregated.set(key, { lcsc, mpn, qty, refs: ref, value, desc, footprint, dnp, excluded, rawCols });
    }
  });

  return { aggregated, warnings };
}

// ── Process BOM text into aggregated parts map + raw rows + warnings ──
export function processBOM(text, fileName) {
  const lines = parseCSV(text);
  if (lines.length < 2) return null;
  const headers = lines[0];
  const cols = detectBOMColumns(headers);
  const rawRows = lines.slice(1);
  const { aggregated, warnings } = aggregateBomRows(rawRows, headers, cols);
  return { headers, cols, rawRows, aggregated, warnings };
}
