// @ts-check
/* inventory-logic.js -- Pure functions for inventory panel logic.
   No DOM, no store, no events. Extracted from inventory-panel.js,
   bom-comparison.js, and bom-row-data.js. */

import { bomKey, invPartKey } from '../part-keys.js';

// ── Dimension fields by part type (for filter chips) ──

var DIMENSION_FIELDS = {
  capacitor: ["dielectric", "tolerance", "voltage"],
  resistor: ["tolerance", "power"],
  inductor: ["tolerance", "current"],
};

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

// ── Generic part grouping ──

/**
 * Match an inventory item to a generic part member by checking all distributor IDs.
 * @param {Object} item - inventory item
 * @param {Map<string, string>} memberToGroup - map from uppercase part_id to generic_part_id
 * @returns {string|null} generic_part_id or null
 */
function matchItemToGroup(item, memberToGroup) {
  var ids = [item.lcsc, item.mpn, item.digikey, item.pololu, item.mouser];
  for (var i = 0; i < ids.length; i++) {
    if (ids[i]) {
      var gpId = memberToGroup.get(ids[i].toUpperCase());
      if (gpId) return gpId;
    }
  }
  return null;
}

/**
 * Group parts by their generic part membership.
 * @param {Array<Object>} parts - inventory items in this section
 * @param {Array<Object>} genericParts - from App.genericParts (has .members array)
 * @returns {{ groups: Array<{ gp: Object, parts: Array<Object> }>, ungrouped: Array<Object> }}
 */
export function groupPartsByGeneric(parts, genericParts) {
  // Build a map from part_id (uppercase) -> generic_part_id
  var memberToGroup = new Map();
  var gpById = {};
  for (var i = 0; i < genericParts.length; i++) {
    var gp = genericParts[i];
    gpById[gp.generic_part_id] = gp;
    if (!gp.members) continue;
    for (var j = 0; j < gp.members.length; j++) {
      memberToGroup.set(gp.members[j].part_id.toUpperCase(), gp.generic_part_id);
    }
  }

  // Group inventory items; track which items are claimed
  var groupMap = {};  // generic_part_id -> Array<Object>
  var claimed = new Set();
  var ungrouped = [];

  for (var k = 0; k < parts.length; k++) {
    var item = parts[k];
    var gpId = matchItemToGroup(item, memberToGroup);
    if (gpId && !claimed.has(k)) {
      claimed.add(k);
      if (!groupMap[gpId]) groupMap[gpId] = [];
      groupMap[gpId].push(item);
    }
  }

  // Collect ungrouped items
  for (var m = 0; m < parts.length; m++) {
    if (!claimed.has(m)) {
      ungrouped.push(parts[m]);
    }
  }

  // Build groups array, sorted by position of first member in original parts array
  var groupEntries = [];
  var gpIds = Object.keys(groupMap);
  for (var n = 0; n < gpIds.length; n++) {
    var id = gpIds[n];
    // Find the first index of any member in the original parts array
    var firstIdx = parts.length;
    for (var p = 0; p < parts.length; p++) {
      if (matchItemToGroup(parts[p], memberToGroup) === id) {
        firstIdx = p;
        break;
      }
    }
    groupEntries.push({ gp: gpById[id], parts: groupMap[id], firstIdx: firstIdx });
  }
  groupEntries.sort(function (a, b) { return a.firstIdx - b.firstIdx; });

  var groups = [];
  for (var q = 0; q < groupEntries.length; q++) {
    groups.push({ gp: groupEntries[q].gp, parts: groupEntries[q].parts });
  }

  return { groups: groups, ungrouped: ungrouped };
}

// ── Filter chip dimensions ──

/**
 * Compute filter chip dimensions from generic part members' specs.
 * Returns dimensions where members have 2+ distinct values.
 * @param {Array<Object>} members - gp.members array (each has .spec)
 * @param {string} partType - "capacitor", "resistor", "inductor"
 * @returns {Array<{ field: string, values: Array<string> }>}
 */
export function computeFilterDimensions(members, partType) {
  var fields = DIMENSION_FIELDS[partType];
  if (!fields) return [];

  var dimensions = [];
  for (var i = 0; i < fields.length; i++) {
    var field = fields[i];
    var valueSet = {};
    for (var j = 0; j < members.length; j++) {
      var spec = members[j].spec;
      if (!spec) continue;
      var raw = spec[field];
      if (raw === undefined || raw === null || raw === "") continue;
      var display = String(raw);
      if (field === "voltage") display = display + "V";
      valueSet[display] = true;
    }
    var values = Object.keys(valueSet);
    if (values.length >= 2) {
      dimensions.push({ field: field, values: values });
    }
  }
  return dimensions;
}

/**
 * Filter members by active chip selections (AND across dimensions).
 * @param {Array<Object>} members - gp.members with .spec
 * @param {Object} activeFilters - { field: displayValue, ... }
 * @returns {Array<Object>} filtered members
 */
export function filterMembersByChips(members, activeFilters) {
  if (!activeFilters) return members;
  var filterKeys = Object.keys(activeFilters);
  if (filterKeys.length === 0) return members;

  return members.filter(function (m) {
    if (!m.spec) return false;
    for (var i = 0; i < filterKeys.length; i++) {
      var field = filterKeys[i];
      var expected = activeFilters[field];
      var raw = m.spec[field];
      if (raw === undefined || raw === null || raw === "") return false;
      var display = String(raw);
      if (field === "voltage") display = display + "V";
      if (display !== expected) return false;
    }
    return true;
  });
}
