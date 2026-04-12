// @ts-check
/* flyout-logic.js — Pure functions for generic part flyout */

// No DOM, no store, no events. All data passed as parameters.

/**
 * Map from spec field names to their dimension name.
 * value_display is the display form of the value field.
 * @type {Object.<string, string>}
 */
var FIELD_TO_DIMENSION = {
  value_display: "value",
  package:       "package",
  voltage:       "voltage",
  tolerance:     "tolerance",
  dielectric:    "dielectric",
  power:         "power",
  current:       "current",
};

/**
 * Spec fields that can appear in group spec (enabled tags come from here).
 * @type {string[]}
 */
var GROUP_SPEC_FIELDS = ["value_display", "package"];

/**
 * Spec fields that can appear in member specs (always-disabled tags).
 * @type {string[]}
 */
var MEMBER_SPEC_FIELDS = ["voltage", "tolerance", "dielectric", "power", "current"];

/**
 * @typedef {{ label: string, dimension: string, enabled: boolean, source: string }} Tag
 */

/**
 * Generate tag objects from a generic part's group spec, strictness, and member specs.
 *
 * Tags from group spec fields (value_display → "value" dim, package → "package" dim) are
 * enabled when the base field name is in strictness.required.
 * Tags from member spec fields (voltage, tolerance, dielectric, power, current) are always
 * disabled, with source='member'.
 * Deduplication is by "dimension:label" key.
 *
 * @param {Object} groupSpec - spec object from the generic part (e.g. { value_display: "100nF", package: "0402" })
 * @param {{ required?: string[] }} strictness - strictness config with optional required array
 * @param {Array<{ spec?: Object }>} members - array of member objects each with a .spec
 * @returns {Tag[]}
 */
export function generateTags(groupSpec, strictness, members) {
  /** @type {Object.<string, Tag>} */
  var seen = {};
  /** @type {Tag[]} */
  var tags = [];

  var required = (strictness && strictness.required) ? strictness.required : [];

  // Tags from group spec fields
  for (var i = 0; i < GROUP_SPEC_FIELDS.length; i++) {
    var field = GROUP_SPEC_FIELDS[i];
    var dimension = FIELD_TO_DIMENSION[field];
    var label = groupSpec ? groupSpec[field] : undefined;
    if (label === undefined || label === null || label === "") continue;
    label = String(label);

    // Base field name for value_display is "value", for package it's "package"
    var baseField = (field === "value_display") ? "value" : field;
    var enabled = (required.indexOf(baseField) !== -1);

    var key = dimension + ":" + label;
    if (!seen[key]) {
      var tag = { label: label, dimension: dimension, enabled: enabled, source: "group" };
      seen[key] = tag;
      tags.push(tag);
    }
  }

  // Tags from member spec fields (always disabled, source='member')
  if (members) {
    for (var m = 0; m < members.length; m++) {
      var spec = members[m] && members[m].spec;
      if (!spec) continue;
      for (var j = 0; j < MEMBER_SPEC_FIELDS.length; j++) {
        var mField = MEMBER_SPEC_FIELDS[j];
        var mDimension = FIELD_TO_DIMENSION[mField];
        var mLabel = spec[mField];
        if (mLabel === undefined || mLabel === null || mLabel === "") continue;
        mLabel = String(mLabel);

        var mKey = mDimension + ":" + mLabel;
        if (!seen[mKey]) {
          var mTag = { label: mLabel, dimension: mDimension, enabled: false, source: "member" };
          seen[mKey] = mTag;
          tags.push(mTag);
        }
      }
    }
  }

  return tags;
}

/**
 * Detect conflicts: two enabled tags sharing the same dimension.
 * Returns array of label strings that conflict.
 * Disabled tags are ignored.
 *
 * @param {Tag[]} tags
 * @returns {string[]} labels of conflicting tags
 */
export function detectConflicts(tags) {
  /** @type {Object.<string, string[]>} */
  var byDimension = {};

  for (var i = 0; i < tags.length; i++) {
    var tag = tags[i];
    if (!tag.enabled) continue;
    if (!byDimension[tag.dimension]) byDimension[tag.dimension] = [];
    byDimension[tag.dimension].push(tag.label);
  }

  /** @type {string[]} */
  var conflicts = [];
  var dims = Object.keys(byDimension);
  for (var d = 0; d < dims.length; d++) {
    var labels = byDimension[dims[d]];
    if (labels.length >= 2) {
      for (var k = 0; k < labels.length; k++) {
        conflicts.push(labels[k]);
      }
    }
  }

  return conflicts;
}

/**
 * Filter members by enabled tags and search text.
 *
 * Filtering logic:
 * - AND across dimensions: a member must match at least one enabled tag per dimension
 * - OR within same dimension (handles conflict case: if two enabled tags share a dimension,
 *   a member matching either is included)
 * - Search text is a substring match against member's description + part_id (case-insensitive)
 * - Empty tags + empty search = return all
 *
 * @param {Array<{ spec?: Object, description?: string, part_id?: string }>} members
 * @param {Tag[]} tags
 * @param {string} searchText
 * @returns {Array<{ spec?: Object, description?: string, part_id?: string }>}
 */
export function filterMembers(members, tags, searchText) {
  if (!members) return [];

  // Group enabled tags by dimension
  /** @type {Object.<string, string[]>} */
  var enabledByDimension = {};
  for (var i = 0; i < tags.length; i++) {
    var tag = tags[i];
    if (!tag.enabled) continue;
    if (!enabledByDimension[tag.dimension]) enabledByDimension[tag.dimension] = [];
    enabledByDimension[tag.dimension].push(tag.label);
  }

  var dims = Object.keys(enabledByDimension);
  var hasTagFilter = dims.length > 0;
  var search = searchText ? searchText.toLowerCase() : "";
  var hasSearch = search.length > 0;

  if (!hasTagFilter && !hasSearch) return members;

  return members.filter(function (member) {
    // Tag filtering: AND across dimensions, OR within dimension
    if (hasTagFilter) {
      var spec = member.spec || {};
      for (var d = 0; d < dims.length; d++) {
        var dim = dims[d];
        var allowedLabels = enabledByDimension[dim];

        // Find the spec field(s) that map to this dimension
        var matched = false;
        var fields = Object.keys(FIELD_TO_DIMENSION);
        for (var f = 0; f < fields.length; f++) {
          if (FIELD_TO_DIMENSION[fields[f]] !== dim) continue;
          var fieldName = fields[f];
          var val = spec[fieldName];
          if (val === undefined || val === null || val === "") continue;
          val = String(val);
          if (allowedLabels.indexOf(val) !== -1) {
            matched = true;
            break;
          }
        }

        if (!matched) return false;
      }
    }

    // Search text filtering
    if (hasSearch) {
      var text = [member.description || "", member.part_id || ""].join(" ").toLowerCase();
      if (text.indexOf(search) === -1) return false;
    }

    return true;
  });
}

/**
 * Generate a default search name from enabled tags: join their labels with a space.
 *
 * @param {Tag[]} tags
 * @returns {string}
 */
export function generateDefaultSearchName(tags) {
  var labels = [];
  for (var i = 0; i < tags.length; i++) {
    if (tags[i].enabled) labels.push(tags[i].label);
  }
  return labels.join(" ");
}
