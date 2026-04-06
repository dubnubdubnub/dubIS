/* bom/bom-logic.js — Pure functions for BOM panel logic.
   No DOM, no store, no events. All data passed as parameters. */

import { isDnp, extractPartIds } from '../csv-parser.js';
import { bomAggKey, invPartKey } from '../part-keys.js';

/**
 * Classify a single BOM row as ok | warn | dnp | subtotal.
 * @param {string[]} row
 * @param {{ lcsc: number, mpn: number, qty: number, ref: number, desc: number, value: number, footprint: number, dnp: number }} bomCols
 * @returns {"ok" | "warn" | "dnp" | "subtotal"}
 */
export function classifyBomRow(row, bomCols) {
  const joined = row.join("").toLowerCase();
  if (joined.includes("subtotal") || joined.includes("total:")) return "subtotal";

  // DNP check before part-ID validation -- DNP rows without MPN/LCSC are not warnings
  if (bomCols.dnp !== -1 && isDnp(row[bomCols.dnp])) return "dnp";

  const { lcsc, mpn } = extractPartIds(row, bomCols);

  if (!lcsc && !mpn) return "warn";

  if (bomCols.qty !== -1) {
    const rawQty = parseInt(row[bomCols.qty], 10);
    if (isNaN(rawQty) || rawQty <= 0) {
      // Only warn if there's actual content in the qty cell
      if ((row[bomCols.qty] || "").trim() !== "") return "warn";
    }
  }

  return "ok";
}

/**
 * Count rows that are warnings or subtotals.
 * @param {string[][]} bomRawRows
 * @param {object} bomCols
 * @returns {number}
 */
export function countBomWarnings(bomRawRows, bomCols) {
  let warns = 0;
  bomRawRows.forEach(row => {
    const cls = classifyBomRow(row, bomCols);
    if (cls === "warn" || cls === "subtotal") warns++;
  });
  return warns;
}

/**
 * Compute effective rows with status, alt qty, etc.
 * @param {Array} results - match results from matchBOM
 * @param {number} multiplier - board quantity multiplier
 * @param {object} links - { manualLinks, confirmedMatches, linkingMode, linkingInvItem, linkingBomRow }
 * @returns {Array|null}
 */
export function computeRows(results, multiplier, links) {
  if (!results) return null;
  const mult = multiplier;
  return results.map(r => {
    let status;
    if (r.bom.dnp) {
      status = "dnp";
    } else if (!r.inv) {
      status = "missing";
    } else if (r.matchType === "value" || r.matchType === "fuzzy") {
      status = "possible";
    } else if (r.matchType === "manual") {
      status = r.bom.qty * mult > r.inv.qty ? "manual-short" : "manual";
    } else if (r.matchType === "confirmed") {
      status = r.bom.qty * mult > r.inv.qty ? "confirmed-short" : "confirmed";
    } else if (r.matchType === "generic") {
      status = r.bom.qty * mult > r.inv.qty ? "generic-short" : "generic";
    } else if (r.bom.qty * mult <= r.inv.qty) {
      status = "ok";
    } else {
      status = "short";
    }
    const altQty = (r.alts || []).reduce((sum, a) => sum + a.qty, 0);
    const combinedQty = (r.inv ? r.inv.qty : 0) + altQty;
    const isShort = status === "short" || status === "manual-short" || status === "confirmed-short";
    const coveredByAlts = (isShort && combinedQty >= r.bom.qty * mult);
    return { ...r, effectiveStatus: status, effectiveQty: r.bom.qty * mult, altQty, combinedQty, coveredByAlts };
  });
}

/**
 * Build a map from bomAggKey -> effectiveStatus for each row.
 * @param {Array} rows - computed rows from computeRows
 * @returns {Object.<string, string>}
 */
export function buildStatusMap(rows) {
  const statusMap = {};
  rows.forEach(r => {
    const statusKey = bomAggKey(r.bom);
    if (statusKey) statusMap[statusKey] = r.effectiveStatus;
  });
  return statusMap;
}

/**
 * Build a Set of bomAggKeys that are linkable (missing, possible, short, etc).
 * @param {Array} rows - computed rows from computeRows
 * @param {boolean} linkingMode - whether linking mode is active
 * @returns {Set<string>}
 */
export function buildLinkableKeys(rows, linkingMode) {
  const missingKeys = new Set();
  if (linkingMode) {
    rows.forEach(r => {
      if (r.effectiveStatus === "missing" || r.effectiveStatus === "possible" || r.effectiveStatus === "short" || r.effectiveStatus === "manual-short" || r.effectiveStatus === "confirmed-short" || r.effectiveStatus === "generic-short") {
        const bsk = bomAggKey(r.bom);
        if (bsk) missingKeys.add(bsk);
      }
    });
  }
  return missingKeys;
}

/**
 * Extract consumption matches from results.
 * @param {Array} results
 * @returns {{ matches: Array, matchesJson: string }}
 */
export function prepareConsumption(results) {
  const matches = [];
  results.forEach(r => {
    if (r.inv && r.matchType !== "value" && r.matchType !== "fuzzy") {
      const pk = invPartKey(r.inv);
      if (pk) matches.push({ part_key: pk, bom_qty: r.bom.qty });
    }
  });
  return { matches, matchesJson: JSON.stringify(matches) };
}

/**
 * Compute price information from computed rows.
 * @param {Array} rows - computed rows from computeRows
 * @param {number} multiplier
 * @returns {{ pricePerBoard: number, totalPrice: number }}
 */
export function computePriceInfo(rows, multiplier) {
  const pricePerBoard = rows.reduce((sum, r) => {
    if (r.inv && r.inv.unit_price > 0) return sum + r.bom.qty * r.inv.unit_price;
    return sum;
  }, 0);
  const totalPrice = pricePerBoard * multiplier;
  return { pricePerBoard, totalPrice };
}
