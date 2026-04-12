/* inv-groups-view.js — Generic-parts grouped view rendering for the inventory panel. */

import { escHtml } from '../ui-helpers.js';
import { App } from '../store.js';
import { openEdit as openGenericEdit } from '../generic-parts-modal.js';

import {
  groupPartsByGeneric,
  computeFilterDimensions,
  filterMembersByChips,
} from './inventory-logic.js';

import state from './inv-state.js';

// ── Groups mode rendering ──

export function renderGroupedView(container, sectionKey, parts, createPartRow, render) {
  var result = groupPartsByGeneric(parts, App.genericParts || []);

  // Render each group
  for (var i = 0; i < result.groups.length; i++) {
    var group = result.groups[i];
    var gp = group.gp;
    var gpParts = group.parts;

    var isExpanded = state.expandedGroups.has(gp.generic_part_id);

    // Compute total qty and part count
    var totalQty = 0;
    for (var q = 0; q < gpParts.length; q++) {
      totalQty += gpParts[q].qty || 0;
    }

    var headerDiv = document.createElement("div");
    headerDiv.className = "generic-group-header";
    headerDiv.innerHTML =
      '<span class="chevron">' + (isExpanded ? "\u25BE" : "\u25B8") + '</span>' +
      '<span class="gp-icon">\u25C6</span>' +
      '<span class="gp-name">' + escHtml(gp.name) + '</span>' +
      '<span class="gp-source">' + escHtml(gp.source || "") + '</span>' +
      '<span class="gp-stats">' + gpParts.length + ' parts</span>' +
      '<span class="gp-total-qty">' + totalQty + '</span>' +
      '<button class="group-edit-btn">Edit</button>';

    // Toggle expanded state on click
    (function (gpId) {
      headerDiv.addEventListener("click", function (e) {
        if (e.target.closest(".group-edit-btn")) return;
        if (state.expandedGroups.has(gpId)) state.expandedGroups.delete(gpId);
        else state.expandedGroups.add(gpId);
        render();
      });
    })(gp.generic_part_id);

    // Edit button handler
    var editBtn = headerDiv.querySelector(".group-edit-btn");
    (function (gpId) {
      editBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        openGenericEdit(gpId);
      });
    })(gp.generic_part_id);

    // In reverse-link mode, make group headers link-targets
    if (App.links.linkingMode && App.links.linkingBomRow) {
      headerDiv.classList.add("link-target");
    }

    container.appendChild(headerDiv);

    // Expanded: render filter row + member part rows
    if (isExpanded) {
      // Filter row
      var filterRowEl = renderFilterRow(gp, gpParts, render);
      if (filterRowEl) container.appendChild(filterRowEl);

      // Apply filters to parts
      var filteredParts = applyGroupFilters(gp.generic_part_id, gpParts, gp);
      for (var j = 0; j < filteredParts.length; j++) {
        var row = createPartRow(filteredParts[j], sectionKey);
        row.classList.add("grouped-part-row");
        container.appendChild(row);
      }
    }
  }

  // Render ungrouped parts (dimmed)
  for (var u = 0; u < result.ungrouped.length; u++) {
    var ungRow = createPartRow(result.ungrouped[u], sectionKey);
    ungRow.classList.add("ungrouped-part-row");
    container.appendChild(ungRow);
  }
}

function renderFilterRow(gp, parts, render) {
  var activeFilters = state.groupFilters[gp.generic_part_id] || {};
  var activeKeys = Object.keys(activeFilters);

  // Compute dimensions from gp.members (not inventory parts)
  var dimensions = computeFilterDimensions(gp.members || [], gp.part_type || "other");
  if (dimensions.length === 0 && activeKeys.length === 0) return null;

  var filterRow = document.createElement("div");
  filterRow.className = "generic-filter-row";

  // Left: dynamic name zone (shown when chips are active)
  var nameZone = document.createElement("div");
  nameZone.className = "filter-name-zone";
  if (activeKeys.length > 0) {
    var dynamicName = gp.name;
    var activeValues = [];
    for (var a = 0; a < activeKeys.length; a++) {
      activeValues.push(activeFilters[activeKeys[a]]);
    }
    dynamicName += " " + activeValues.join(" ");

    var nameSpan = document.createElement("span");
    nameSpan.className = "filter-dynamic-name";
    nameSpan.textContent = dynamicName;
    nameZone.appendChild(nameSpan);

    // In link mode with chips active, make name zone a link-target
    if (App.links.linkingMode && App.links.linkingBomRow) {
      nameZone.classList.add("link-target");
      var hintSpan = document.createElement("span");
      hintSpan.className = "filter-link-hint";
      hintSpan.textContent = "\u2190 click to link";
      nameZone.appendChild(hintSpan);
    }
  }
  filterRow.appendChild(nameZone);

  if (dimensions.length > 0 && activeKeys.length > 0) {
    var sep = document.createElement("span");
    sep.className = "filter-sep";
    sep.textContent = "|";
    filterRow.appendChild(sep);
  }

  // Right: chip zone with dimension labels and chips
  var chipZone = document.createElement("div");
  chipZone.className = "filter-chip-zone";

  for (var d = 0; d < dimensions.length; d++) {
    var dim = dimensions[d];

    // Cross-dimension filtering: count members matching other active filters
    var otherFilters = {};
    for (var f = 0; f < activeKeys.length; f++) {
      if (activeKeys[f] !== dim.field) {
        otherFilters[activeKeys[f]] = activeFilters[activeKeys[f]];
      }
    }
    var crossFiltered = filterMembersByChips(gp.members || [], otherFilters);

    // Count values in the cross-filtered set
    var valueCounts = {};
    for (var c = 0; c < crossFiltered.length; c++) {
      var spec = crossFiltered[c].spec;
      if (!spec) continue;
      var raw = spec[dim.field];
      if (raw === undefined || raw === null || raw === "") continue;
      var display = String(raw);
      if (dim.field === "voltage") display = display + "V";
      valueCounts[display] = (valueCounts[display] || 0) + 1;
    }

    var dimDiv = document.createElement("span");
    dimDiv.className = "filter-dim";

    var label = document.createElement("span");
    label.className = "filter-dim-label";
    label.textContent = dim.field;
    dimDiv.appendChild(label);

    for (var v = 0; v < dim.values.length; v++) {
      var val = dim.values[v];
      var count = valueCounts[val] || 0;
      var isActive = activeFilters[dim.field] === val;

      var chip = document.createElement("span");
      chip.className = "filter-chip" + (isActive ? " active" : "") + (count === 0 && !isActive ? " dim" : "");
      chip.innerHTML = escHtml(val) + '<span class="chip-count">' + count + '</span>';

      // Toggle filter on click
      (function (field, value) {
        chip.addEventListener("click", function () {
          if (!state.groupFilters[gp.generic_part_id]) {
            state.groupFilters[gp.generic_part_id] = {};
          }
          if (state.groupFilters[gp.generic_part_id][field] === value) {
            delete state.groupFilters[gp.generic_part_id][field];
          } else {
            state.groupFilters[gp.generic_part_id][field] = value;
          }
          // Clean up empty filter objects
          if (Object.keys(state.groupFilters[gp.generic_part_id]).length === 0) {
            delete state.groupFilters[gp.generic_part_id];
          }
          render();
        });
      })(dim.field, val);

      dimDiv.appendChild(chip);
    }

    chipZone.appendChild(dimDiv);
  }

  filterRow.appendChild(chipZone);
  return filterRow;
}

function applyGroupFilters(gpId, parts, gp) {
  var activeFilters = state.groupFilters[gpId];
  if (!activeFilters || Object.keys(activeFilters).length === 0) return parts;

  // Get filtered member part_ids from gp.members
  var filteredMembers = filterMembersByChips(gp.members || [], activeFilters);
  var allowedIds = new Set();
  for (var i = 0; i < filteredMembers.length; i++) {
    allowedIds.add(filteredMembers[i].part_id.toUpperCase());
  }

  // Filter inventory parts to only those whose IDs match filtered members
  return parts.filter(function (item) {
    var ids = [item.lcsc, item.mpn, item.digikey, item.pololu, item.mouser];
    for (var j = 0; j < ids.length; j++) {
      if (ids[j] && allowedIds.has(ids[j].toUpperCase())) return true;
    }
    return false;
  });
}
