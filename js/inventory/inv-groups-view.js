/* inv-groups-view.js — Generic-parts groups view for the inventory panel.
   Extracted from inventory-panel.js. Renders group headers, filter rows,
   and grouped part rows. Receives createPartRow and render as parameters. */

import { escHtml } from '../ui-helpers.js';
import { store } from '../store.js';
import { openEdit as openGenericEdit } from '../generic-parts-modal.js';
import {
  groupPartsByGeneric,
  computeFilterDimensions,
  filterMembersByChips,
} from './inventory-logic.js';
import state from './inv-state.js';

// ── Groups mode rendering ──

export function renderGroupedView(container, sectionKey, parts, createPartRow, render) {
  const result = groupPartsByGeneric(parts, store.genericParts || []);

  // Render each group
  for (let i = 0; i < result.groups.length; i++) {
    const group = result.groups[i];
    const gp = group.gp;
    const gpParts = group.parts;

    const gpId = gp.generic_part_id;
    const isExpanded = state.expandedGroups.has(gpId);

    // Compute total qty and part count
    let totalQty = 0;
    for (let q = 0; q < gpParts.length; q++) {
      totalQty += gpParts[q].qty || 0;
    }

    const headerDiv = document.createElement("div");
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
    headerDiv.addEventListener("click", function (e) {
      if (e.target.closest(".group-edit-btn")) return;
      if (state.expandedGroups.has(gpId)) state.expandedGroups.delete(gpId);
      else state.expandedGroups.add(gpId);
      render();
    });

    // Edit button handler
    const editBtn = headerDiv.querySelector(".group-edit-btn");
    editBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openGenericEdit(gpId);
    });

    // In reverse-link mode, make group headers link-targets
    if (store.links.linkingMode && store.links.linkingBomRow) {
      headerDiv.classList.add("link-target");
    }

    container.appendChild(headerDiv);

    // Expanded: render filter row + member part rows
    if (isExpanded) {
      // Filter row
      const filterRowEl = renderFilterRow(gp, gpParts, render);
      if (filterRowEl) container.appendChild(filterRowEl);

      // Apply filters to parts
      const filteredParts = applyGroupFilters(gpId, gpParts, gp);
      for (let j = 0; j < filteredParts.length; j++) {
        const row = createPartRow(filteredParts[j], sectionKey);
        row.classList.add("grouped-part-row");
        container.appendChild(row);
      }
    }
  }

  // Render ungrouped parts (dimmed)
  for (let u = 0; u < result.ungrouped.length; u++) {
    const ungRow = createPartRow(result.ungrouped[u], sectionKey);
    ungRow.classList.add("ungrouped-part-row");
    container.appendChild(ungRow);
  }
}

function renderFilterRow(gp, parts, render) {
  const gpId = gp.generic_part_id;
  const activeFilters = state.groupFilters[gpId] || {};
  const activeKeys = Object.keys(activeFilters);

  // Compute dimensions from gp.members (not inventory parts)
  const dimensions = computeFilterDimensions(gp.members || [], gp.part_type || "other");
  if (dimensions.length === 0 && activeKeys.length === 0) return null;

  const filterRow = document.createElement("div");
  filterRow.className = "generic-filter-row";

  // Left: dynamic name zone (shown when chips are active)
  const nameZone = document.createElement("div");
  nameZone.className = "filter-name-zone";
  if (activeKeys.length > 0) {
    const activeValues = [];
    for (let a = 0; a < activeKeys.length; a++) {
      activeValues.push(activeFilters[activeKeys[a]]);
    }
    const dynamicName = gp.name + " " + activeValues.join(" ");

    const nameSpan = document.createElement("span");
    nameSpan.className = "filter-dynamic-name";
    nameSpan.textContent = dynamicName;
    nameZone.appendChild(nameSpan);

    // In link mode with chips active, make name zone a link-target
    if (store.links.linkingMode && store.links.linkingBomRow) {
      nameZone.classList.add("link-target");
      const hintSpan = document.createElement("span");
      hintSpan.className = "filter-link-hint";
      hintSpan.textContent = "\u2190 click to link";
      nameZone.appendChild(hintSpan);
    }
  }
  filterRow.appendChild(nameZone);

  if (dimensions.length > 0 && activeKeys.length > 0) {
    const sep = document.createElement("span");
    sep.className = "filter-sep";
    sep.textContent = "|";
    filterRow.appendChild(sep);
  }

  // Right: chip zone with dimension labels and chips
  const chipZone = document.createElement("div");
  chipZone.className = "filter-chip-zone";

  for (let d = 0; d < dimensions.length; d++) {
    const dim = dimensions[d];

    // Cross-dimension filtering: count members matching other active filters
    const otherFilters = {};
    for (let f = 0; f < activeKeys.length; f++) {
      if (activeKeys[f] !== dim.field) {
        otherFilters[activeKeys[f]] = activeFilters[activeKeys[f]];
      }
    }
    const crossFiltered = filterMembersByChips(gp.members || [], otherFilters);

    // Count values in the cross-filtered set
    const valueCounts = {};
    for (let c = 0; c < crossFiltered.length; c++) {
      const spec = crossFiltered[c].spec;
      if (!spec) continue;
      const raw = spec[dim.field];
      if (raw === undefined || raw === null || raw === "") continue;
      let display = String(raw);
      if (dim.field === "voltage") display = display + "V";
      valueCounts[display] = (valueCounts[display] || 0) + 1;
    }

    const dimDiv = document.createElement("span");
    dimDiv.className = "filter-dim";

    const label = document.createElement("span");
    label.className = "filter-dim-label";
    label.textContent = dim.field;
    dimDiv.appendChild(label);

    for (let v = 0; v < dim.values.length; v++) {
      const val = dim.values[v];
      const count = valueCounts[val] || 0;
      const isActive = activeFilters[dim.field] === val;

      const chip = document.createElement("span");
      chip.className = "filter-chip" + (isActive ? " active" : "") + (count === 0 && !isActive ? " dim" : "");
      chip.innerHTML = escHtml(val) + '<span class="chip-count">' + count + '</span>';

      // Toggle filter on click — dim.field and val are block-scoped by const in the for-loop
      const field = dim.field;
      chip.addEventListener("click", function () {
        if (!state.groupFilters[gpId]) {
          state.groupFilters[gpId] = {};
        }
        if (state.groupFilters[gpId][field] === val) {
          delete state.groupFilters[gpId][field];
        } else {
          state.groupFilters[gpId][field] = val;
        }
        // Clean up empty filter objects
        if (Object.keys(state.groupFilters[gpId]).length === 0) {
          delete state.groupFilters[gpId];
        }
        render();
      });

      dimDiv.appendChild(chip);
    }

    chipZone.appendChild(dimDiv);
  }

  filterRow.appendChild(chipZone);
  return filterRow;
}

function applyGroupFilters(gpId, parts, gp) {
  const activeFilters = state.groupFilters[gpId];
  if (!activeFilters || Object.keys(activeFilters).length === 0) return parts;

  // Get filtered member part_ids from gp.members
  const filteredMembers = filterMembersByChips(gp.members || [], activeFilters);
  const allowedIds = new Set();
  for (let i = 0; i < filteredMembers.length; i++) {
    allowedIds.add(filteredMembers[i].part_id.toUpperCase());
  }

  // Filter inventory parts to only those whose IDs match filtered members
  return parts.filter(function (item) {
    const ids = [item.lcsc, item.mpn, item.digikey, item.pololu, item.mouser];
    for (let j = 0; j < ids.length; j++) {
      if (ids[j] && allowedIds.has(ids[j].toUpperCase())) return true;
    }
    return false;
  });
}
