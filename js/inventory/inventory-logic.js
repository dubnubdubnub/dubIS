// @ts-check
/* inventory-logic.js -- Pure functions for inventory panel logic.
   No DOM, no store, no events. Extracted from inventory-panel.js,
   bom-comparison.js, and bom-row-data.js. */

import { bomKey, invPartKey } from '../part-keys.js';

// ── Re-export bomRowDisplayData (moved from bom-row-data.js) ──

export { bomRowDisplayData } from '../bom-row-data.js';

// ── Inventory grouping ──

/**
 * Group inventory items by their section, keyed by section name.
 * @param {Array<{section?: string}>} inventory
 * @returns {Record<string, Array<{section?: string}>>}
 */
export function groupBySection(inventory) {
  /** @type {Record<string, Array<{section?: string}>>} */
  var sections = {};
  for (var i = 0; i < inventory.length; i++) {
    var item = inventory[i];
    var sec = item.section || "Other";
    if (!sections[sec]) sections[sec] = [];
    sections[sec].push(item);
  }
  return sections;
}

// ── Search filtering ──

/**
 * Filter parts by a lowercase search query.
 * @param {Array<Object>} parts
 * @param {string} query - lowercase search term
 * @returns {Array<Object>}
 */
export function filterByQuery(parts, query) {
  if (!query) return parts;
  return parts.filter(function (item) {
    var text = [item.lcsc, item.mpn, item.description, item.manufacturer, item.package, item.digikey, item.pololu, item.mouser]
      .join(" ").toLowerCase();
    return text.includes(query);
  });
}

// ── Distributor inference ──

/**
 * Infer which distributor a part comes from based on populated PN fields.
 * Priority: lcsc > digikey > mouser > pololu > other.
 * @param {Object} item - inventory item
 * @returns {"lcsc"|"digikey"|"mouser"|"pololu"|"other"}
 */
export function inferDistributor(item) {
  if (item.lcsc) return "lcsc";
  if (item.digikey) return "digikey";
  if (item.mouser) return "mouser";
  if (item.pololu) return "pololu";
  return "other";
}

/**
 * Count inventory items per distributor.
 * @param {Array<Object>} inventory
 * @returns {{lcsc: number, digikey: number, mouser: number, pololu: number, other: number}}
 */
export function countByDistributor(inventory) {
  var counts = { lcsc: 0, digikey: 0, mouser: 0, pololu: 0, other: 0 };
  for (var i = 0; i < inventory.length; i++) {
    counts[inferDistributor(inventory[i])]++;
  }
  return counts;
}

/**
 * Filter parts by distributor. Returns all parts when filter is null.
 * @param {Array<Object>} parts
 * @param {string|null} distributor - "lcsc", "digikey", "mouser", "pololu", "other", or null
 * @returns {Array<Object>}
 */
export function filterByDistributor(parts, distributor) {
  if (!distributor) return parts;
  return parts.filter(function (item) {
    return inferDistributor(item) === distributor;
  });
}

// ── Collapsed state ──

/**
 * Check if a section is collapsed.
 * @param {Set<string>} collapsedSections
 * @param {string} section
 * @returns {boolean}
 */
export function computeCollapsedState(collapsedSections, section) {
  return collapsedSections.has(section);
}

// ── BOM matched inventory keys ──

/**
 * Compute the set of inventory part keys that are BOM-matched.
 * @param {{rows: Array<{inv?: Object}>}} bomData
 * @returns {Set<string>}
 */
export function computeMatchedInvKeys(bomData) {
  var matchedInvKeys = new Set();
  if (!bomData || !bomData.rows) return matchedInvKeys;
  var rows = bomData.rows;
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].inv) {
      var pk = invPartKey(rows[i].inv).toUpperCase();
      if (pk) matchedInvKeys.add(pk);
    }
  }
  return matchedInvKeys;
}

// ── BOM status sort order ──

export var BOM_STATUS_SORT_ORDER = {
  missing: 0, "manual-short": 0.4, manual: 0.5,
  "confirmed-short": 0.7, confirmed: 0.75,
  possible: 1, short: 2, ok: 3, dnp: 4,
};

/**
 * Sort BOM rows by status priority.
 * @param {Array<{effectiveStatus: string}>} rows
 * @returns {Array<{effectiveStatus: string}>}
 */
export function sortBomRows(rows) {
  return [...rows].sort(function (a, b) {
    return (BOM_STATUS_SORT_ORDER[a.effectiveStatus] || 0) - (BOM_STATUS_SORT_ORDER[b.effectiveStatus] || 0);
  });
}

/**
 * Build a Map from partKey -> row for BOM rows (used for delegation).
 * @param {Array<Object>} sortedRows
 * @returns {Map<string, Object>}
 */
export function buildRowMap(sortedRows) {
  var map = new Map();
  for (var i = 0; i < sortedRows.length; i++) {
    map.set(bomKey(sortedRows[i].bom), sortedRows[i]);
  }
  return map;
}
