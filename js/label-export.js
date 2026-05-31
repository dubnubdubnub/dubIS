// @ts-check
/* label-export.js — Pure formatting + measurement logic for Epson label CSV export */

// ── Vendor field map ─────────────────────────────────────────────────────────
const VENDOR_FIELD = {
  v_lcsc:    "lcsc",
  v_digikey: "digikey",
  v_mouser:  "mouser",
  v_pololu:  "pololu",
};

const FALLBACK_ORDER = ["v_lcsc", "v_digikey", "v_mouser", "v_pololu"];

// ── pickDistributor ──────────────────────────────────────────────────────────
/**
 * @param {object} item - inventory item
 * @returns {{ vendorId: string, number: string }}
 */
export function pickDistributor(item) {
  // Try primary_vendor_id first
  const primaryId = item.primary_vendor_id;
  if (primaryId && VENDOR_FIELD[primaryId]) {
    const val = item[VENDOR_FIELD[primaryId]];
    if (val) return { vendorId: primaryId, number: val };
  }

  // Fallback order
  for (const vendorId of FALLBACK_ORDER) {
    const field = VENDOR_FIELD[vendorId];
    const val = item[field];
    if (val) return { vendorId, number: val };
  }

  return { vendorId: "v_unknown", number: "" };
}

// ── estimateWidthMm ──────────────────────────────────────────────────────────
/**
 * @param {string} text
 * @param {number} fontPt
 * @param {object} cfg
 * @returns {number}
 */
export function estimateWidthMm(text, fontPt, cfg) {
  if (!text) return 0;
  let total = 0;
  for (const ch of text) {
    if (cfg.narrow_chars && cfg.narrow_chars.includes(ch)) {
      total += cfg.char_width.narrow;
    } else if (cfg.wide_chars && cfg.wide_chars.includes(ch)) {
      total += cfg.char_width.wide;
    } else {
      total += cfg.char_width.default;
    }
  }
  return total * fontPt * cfg.calibration_k;
}

// ── wrapBalanced ─────────────────────────────────────────────────────────────
/**
 * Split text on spaces into two balanced lines, minimising max(width(A), width(B)).
 * @param {string} text
 * @param {number} fontPt
 * @param {object} cfg
 * @returns {[string, string]}
 */
function wrapBalanced(text, fontPt, cfg) {
  const words = text.split(" ").filter(w => w.length > 0);
  if (words.length <= 1) return [text, ""];

  let bestMax = Infinity;
  let bestA = text;
  let bestB = "";

  for (let i = 1; i < words.length; i++) {
    const lineA = words.slice(0, i).join(" ");
    const lineB = words.slice(i).join(" ");
    const wA = estimateWidthMm(lineA, fontPt, cfg);
    const wB = estimateWidthMm(lineB, fontPt, cfg);
    const maxW = Math.max(wA, wB);
    if (maxW < bestMax) {
      bestMax = maxW;
      bestA = lineA;
      bestB = lineB;
    }
  }

  return [bestA, bestB];
}

// ── format6mm ────────────────────────────────────────────────────────────────
/**
 * @param {object} item - inventory item
 * @param {object} cfg
 * @returns {{ vendorId: string, number: string, text: string, columns: string[], estMm: number, warnings: string[] }}
 */
export function format6mm(item, cfg) {
  const { vendorId, number } = pickDistributor(item);
  const prefix = cfg.distributor_prefix[vendorId] || "";
  const numberToken = number ? prefix + number : "";

  const tokens = [item.mpn, numberToken, item.description].filter(t => t);
  const text = tokens.join(" ");

  const estMm = estimateWidthMm(text, cfg.tape6.font_pt, cfg);
  const warnings = [];
  if (estMm > cfg.tape6.budget_mm) warnings.push("over-budget");

  return { vendorId, number, text, columns: [text], estMm, warnings };
}

// ── format12mm ───────────────────────────────────────────────────────────────
/**
 * @param {object} item - inventory item
 * @param {object} cfg
 * @returns {{ vendorId: string, number: string, lines: string[], columns: string[], estMm: number, warnings: string[] }}
 */
export function format12mm(item, cfg) {
  const { vendorId, number } = pickDistributor(item);
  const prefix = cfg.distributor_prefix[vendorId] || "";
  const numberToken = number ? prefix + number : "";

  const [dA, dB] = wrapBalanced(item.description || "", cfg.tape12.font_pt, cfg);

  // Variant A: row1 = mpn only
  const row1A = item.mpn || "";
  const rowsA = [row1A, dA, dB];
  const labelWidthA = Math.max(
    ...rowsA.map(r => r ? estimateWidthMm(r, cfg.tape12.font_pt, cfg) : 0)
  );

  // Variant B: row1 = mpn + numberToken (only applicable if numberToken non-empty)
  let chosen;
  if (numberToken) {
    const row1B = [item.mpn, numberToken].filter(t => t).join(" ");
    const rowsB = [row1B, dA, dB];
    const labelWidthB = Math.max(
      ...rowsB.map(r => r ? estimateWidthMm(r, cfg.tape12.font_pt, cfg) : 0)
    );

    // Choose smaller; on tie prefer B (include number)
    if (labelWidthB <= labelWidthA) {
      chosen = { rows: rowsB, labelWidth: labelWidthB };
    } else {
      chosen = { rows: rowsA, labelWidth: labelWidthA };
    }
  } else {
    chosen = { rows: rowsA, labelWidth: labelWidthA };
  }

  const lines = chosen.rows;
  const estMm = chosen.labelWidth;

  const warnings = [];
  if (estMm > cfg.tape12.budget_mm) {
    warnings.push("over-budget");
  } else if (estMm > cfg.tape12.preferred_mm) {
    warnings.push("over-preferred");
  }

  return { vendorId, number, lines, columns: lines, estMm, warnings };
}

// ── buildLabels ──────────────────────────────────────────────────────────────
/**
 * @param {object[]} items
 * @param {"6mm"|"12mm"} tape
 * @param {object} cfg
 * @returns {object[]}
 */
export function buildLabels(items, tape, cfg) {
  if (tape === "6mm") return items.map(item => format6mm(item, cfg));
  return items.map(item => format12mm(item, cfg));
}

// ── CSV escaping helper ───────────────────────────────────────────────────────
function csvEscape(val) {
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  const s = val == null ? "" : String(val);
  if (s.indexOf(",") !== -1 || s.indexOf('"') !== -1 || s.indexOf("\r") !== -1 || s.indexOf("\n") !== -1) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// ── toCsvByDistributor ───────────────────────────────────────────────────────
const UTF8_BOM = "﻿";

/**
 * @param {object[]} results
 * @param {"6mm"|"12mm"} tape
 * @param {object} cfg
 * @returns {Map<string, string>}
 */
export function toCsvByDistributor(results, tape, cfg) {
  // Group by vendorId
  /** @type {Map<string, object[]>} */
  const groups = new Map();
  for (const result of results) {
    const g = groups.get(result.vendorId) || [];
    g.push(result);
    groups.set(result.vendorId, g);
  }

  const is6mm = tape === "6mm";
  const headers = is6mm ? ["Label"] : ["Line1", "Line2", "Line3"];

  /** @type {Map<string, string>} */
  const out = new Map();
  for (const [vendorId, group] of groups) {
    const rows = [];
    if (cfg.header_row) rows.push(headers.map(csvEscape).join(","));
    for (const result of group) {
      rows.push(result.columns.map(csvEscape).join(","));
    }
    out.set(vendorId, UTF8_BOM + rows.join("\r\n") + "\r\n");
  }

  return out;
}
