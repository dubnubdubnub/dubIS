/* csv-parser.js — RFC 4180-compliant CSV parser and column detection */

// ── Parse CSV (handles quoted fields with commas/newlines) ──
function parseCSV(text) {
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
      } else if (c === ',') {
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
function detectBOMColumns(headers) {
  const cols = { lcsc: -1, mpn: -1, qty: -1, ref: -1, desc: -1, value: -1, footprint: -1 };
  const lower = headers.map(h => h.toLowerCase());
  lower.forEach((h, i) => {
    if (cols.lcsc === -1 && (/lcsc/.test(h) || /jlcpcb/.test(h) || (h.includes("part") && (h.includes("#") || h.includes("number"))))) cols.lcsc = i;
    if (cols.ref === -1 && (/designator/.test(h) || /reference/.test(h))) cols.ref = i;
    if (cols.qty === -1 && (/^qty$/.test(h) || /quantity/.test(h))) cols.qty = i;
    if (cols.desc === -1 && (/description/.test(h) || /^desc$/.test(h))) cols.desc = i;
    if (cols.value === -1 && h === "value") cols.value = i;
    if (cols.mpn === -1 && (/^mpn$/.test(h) || /manufacture\s*part/.test(h))) cols.mpn = i;
    if (cols.footprint === -1 && /footprint/.test(h)) cols.footprint = i;
  });
  // If no explicit MPN column but there is a Value column, use Value as MPN fallback
  if (cols.mpn === -1 && cols.value !== -1) cols.mpn = cols.value;
  return cols;
}

// ── Check if a string looks like an LCSC part number ──
function looksLikeLCSC(s) {
  return /^C\d{3,}$/i.test(s);
}

// ── Extract LCSC part number from a value string ──
function extractLCSC(s) {
  const m = s.match(/\b(C\d{4,})\b/i);
  return m ? m[1].toUpperCase() : null;
}

// ── Process BOM text into aggregated parts map ──
function processBOM(text, fileName) {
  const lines = parseCSV(text);
  if (lines.length < 2) return null;

  const headers = lines[0];
  const cols = detectBOMColumns(headers);
  const dataRows = lines.slice(1);
  const aggregated = new Map();

  dataRows.forEach(row => {
    let lcsc = cols.lcsc !== -1 ? (row[cols.lcsc] || "").trim() : "";
    let mpn = cols.mpn !== -1 ? (row[cols.mpn] || "").trim() : "";
    let qty = cols.qty !== -1 ? parseInt(row[cols.qty], 10) || 1 : 1;
    let ref = cols.ref !== -1 ? (row[cols.ref] || "").trim() : "";
    let desc = cols.desc !== -1 ? (row[cols.desc] || "").trim() : "";
    let value = cols.value !== -1 ? (row[cols.value] || "").trim() : "";
    let footprint = cols.footprint !== -1 ? (row[cols.footprint] || "").trim() : "";

    // Try to extract LCSC from MPN/Value if no explicit LCSC column
    if (!lcsc && mpn) {
      const extracted = extractLCSC(mpn);
      if (extracted) lcsc = extracted;
    }

    if (!lcsc && !mpn) return;

    const key = lcsc ? lcsc.toUpperCase() : mpn.toUpperCase();

    if (aggregated.has(key)) {
      const existing = aggregated.get(key);
      existing.qty += qty;
      if (ref) {
        if (existing.refs) existing.refs += ", " + ref;
        else existing.refs = ref;
      }
    } else {
      aggregated.set(key, {
        lcsc: lcsc.toUpperCase(),
        mpn: mpn,
        qty: qty,
        refs: ref,
        value: value,
        desc: desc,
        footprint: footprint,
      });
    }
  });

  return aggregated;
}
