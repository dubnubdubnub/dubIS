/* inventory/inv-render.js — Section/hierarchy rendering for the inventory panel.
   renderNormalInventory, renderGlobalScope, appendFlatRows, renderVendorPiles,
   renderHierarchySection, renderSubSection, renderSection. */

import { escHtml } from '../ui-helpers.js';
import { store } from '../store.js';
import {
  groupBySection,
  filterByQuery,
  filterByDistributor,
  filterByVendor,
} from './inventory-logic.js';
import { filterByPredicate } from './filter-chips-fields.js';
import state from './inv-state.js';
import { sortPartsBy, groupByVendor } from './inv-sort-group.js';
import { createPartRow } from './inv-row-build.js';
import { renderGroupedView } from './inv-groups-view.js';

// ── Section hierarchy (read once from store) ──
var SECTION_HIERARCHY = store.SECTION_HIERARCHY;
var FLAT_SECTIONS = store.FLAT_SECTIONS;

// ── Helpers: active scope/level in BOM vs normal mode ──

// In BOM mode, the column header is hidden — sort/vendor-group state must NOT
// leak into the "Other Inventory" rendering path.
function activeSortScope()        { return state.bomData ? null : state.sortScope; }
function activeVendorGroupScope() { return state.bomData ? null : state.vendorGroupScope; }
function activeGroupLevel()       { return state.bomData ? 0    : state.groupLevel; }

// ── Normal mode: grouped by section ──

export function renderNormalInventory() {
  var query = (state.searchInput.value || "").toLowerCase();
  var sections = groupBySection(store.inventory);

  // ── Global scope: flatten everything, no section/subsection headers ──
  if (activeSortScope() === "global" || activeVendorGroupScope() === "global" || activeGroupLevel() === 2) {
    renderGlobalScope(sections, query);
    return;
  }

  // ── Section/subsection rendering ──
  for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
    var entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      var filtered = filterByPredicate(filterByVendor(filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributors), state.selectedVendorIds), state.activePredicate);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, sections, query);
    }
  }
}

function sectionDisplayName(fullKey) {
  var sep = fullKey.indexOf(" > ");
  return sep === -1 ? fullKey : fullKey.substring(sep + 3);
}

export function renderGlobalScope(sections, query) {
  var allParts = [];
  for (var i = 0; i < FLAT_SECTIONS.length; i++) {
    var name = FLAT_SECTIONS[i];
    var bucket = sections[name] || [];
    var filtered = filterByPredicate(filterByDistributor(filterByQuery(bucket, query), state.activeDistributors), state.activePredicate);
    var displayName = sectionDisplayName(name);
    for (var j = 0; j < filtered.length; j++) {
      var tagged = Object.assign({}, filtered[j]);
      tagged.__sectionName = displayName;
      allParts.push(tagged);
    }
  }
  if (allParts.length === 0) return;

  if (activeVendorGroupScope() === "global") {
    renderVendorPiles(state.body, allParts, "global");
  } else {
    var sorted = sortPartsBy(allParts, state.sortColumn);
    appendFlatRows(state.body, sorted, "global");
  }
}

export function appendFlatRows(container, parts, scopeKey) {
  for (var k = 0; k < parts.length; k++) {
    var sectionChip = activeGroupLevel() === 2 ? parts[k].__sectionName : undefined;
    var row = createPartRow(parts[k], scopeKey, sectionChip);
    container.appendChild(row);
  }
}

export function renderVendorPiles(container, parts, scopeKey) {
  var piles = groupByVendor(parts);
  for (var p = 0; p < piles.length; p++) {
    var hdr = document.createElement("div");
    hdr.className = "inv-vendor-header";
    hdr.textContent = piles[p].vendor.charAt(0).toUpperCase() + piles[p].vendor.slice(1) + " (" + piles[p].parts.length + ")";
    container.appendChild(hdr);
    var pileSorted = (state.bomData ? null : state.sortColumn) ? sortPartsBy(piles[p].parts, state.sortColumn) : piles[p].parts;
    appendFlatRows(container, pileSorted, scopeKey + ":" + piles[p].vendor);
  }
}

export function renderHierarchySection(entry, sections, query) {
  var parentParts = filterByPredicate(filterByVendor(filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributors), state.selectedVendorIds), state.activePredicate);
  var childData = [];
  var totalCount = parentParts.length;
  for (var i = 0; i < entry.children.length; i++) {
    var fullKey = entry.name + " > " + entry.children[i];
    var filtered = filterByPredicate(filterByVendor(filterByDistributor(filterByQuery(sections[fullKey] || [], query), state.activeDistributors), state.selectedVendorIds), state.activePredicate);
    totalCount += filtered.length;
    childData.push({ name: entry.children[i], fullKey: fullKey, parts: filtered });
  }
  if (totalCount === 0) return;

  var container = document.createElement("div");
  container.className = "inv-section";

  var isParentCollapsed = state.collapsedSections.has(entry.name);
  var header = document.createElement("div");
  header.className = "inv-parent-header" + (isParentCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(entry.name) + ' <span class="inv-section-count">(' + totalCount + ')</span>';
  header.addEventListener("click", function () {
    if (state.collapsedSections.has(entry.name)) state.collapsedSections.delete(entry.name);
    else state.collapsedSections.add(entry.name);
    state._render();
  });
  container.appendChild(header);

  if (isParentCollapsed) { state.body.appendChild(container); return; }

  // ── Section-scope sort or vendor-group: merge subsections, render flat under section header ──
  if (activeSortScope() === "section" || activeVendorGroupScope() === "section") {
    var merged = parentParts.slice();
    for (var c = 0; c < childData.length; c++) merged = merged.concat(childData[c].parts);
    if (activeVendorGroupScope() === "section") {
      renderVendorPiles(container, merged, entry.name);
    } else {
      var sortedSec = sortPartsBy(merged, state.sortColumn);
      for (var s = 0; s < sortedSec.length; s++) container.appendChild(createPartRow(sortedSec[s], entry.name));
    }
    state.body.appendChild(container);
    return;
  }

  // ── Group level 1 (sections only): merge subsections without subsection headers ──
  if (activeGroupLevel() === 1) {
    var allChildParts = parentParts.slice();
    for (var cc = 0; cc < childData.length; cc++) allChildParts = allChildParts.concat(childData[cc].parts);
    for (var x = 0; x < allChildParts.length; x++) container.appendChild(createPartRow(allChildParts[x], entry.name));
    state.body.appendChild(container);
    return;
  }

  // ── Default rendering: subsection headers visible ──
  if (parentParts.length > 0) renderSubSection(container, "Ungrouped", entry.name, parentParts);
  for (var j = 0; j < childData.length; j++) {
    if (childData[j].parts.length > 0) renderSubSection(container, childData[j].name, childData[j].fullKey, childData[j].parts);
  }
  state.body.appendChild(container);
}

export function renderSubSection(container, displayName, fullKey, parts) {
  var sub = document.createElement("div");
  sub.className = "inv-subsection";

  var isCollapsed = state.collapsedSections.has(fullKey);
  var hasGroups = store.genericParts && store.genericParts.length > 0;
  var groupsActive = state.groupsSections.has(fullKey);

  var header = document.createElement("div");
  header.className = "inv-subsection-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(displayName) + ' <span class="inv-section-count">(' + parts.length + ')</span>' +
    (hasGroups ? '<button class="groups-btn' + (groupsActive ? ' active' : '') + '">◆ Groups</button>' : '');

  header.addEventListener("click", function (e) {
    if (e.target.closest(".groups-btn")) return;
    if (state.collapsedSections.has(fullKey)) state.collapsedSections.delete(fullKey);
    else state.collapsedSections.add(fullKey);
    state._render();
  });
  var groupsBtn = header.querySelector(".groups-btn");
  if (groupsBtn) {
    groupsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (state.groupsSections.has(fullKey)) state.groupsSections.delete(fullKey);
      else state.groupsSections.add(fullKey);
      state._render();
    });
  }
  sub.appendChild(header);

  if (!isCollapsed) {
    if (groupsActive) {
      renderGroupedView(sub, fullKey, parts);
    } else if (activeVendorGroupScope() === "subsection") {
      renderVendorPiles(sub, parts, fullKey);
    } else {
      var subSorted = activeSortScope() === "subsection" && state.sortColumn ? sortPartsBy(parts, state.sortColumn) : parts;
      for (var k = 0; k < subSorted.length; k++) sub.appendChild(createPartRow(subSorted[k], fullKey));
    }
  }
  container.appendChild(sub);
}

// ── Flat section renderer ──

export function renderSection(name, parts) {
  var section = document.createElement("div");
  section.className = "inv-section";

  var isCollapsed = state.collapsedSections.has(name);
  var hasGroups = store.genericParts && store.genericParts.length > 0;
  var groupsActive = state.groupsSections.has(name);

  var header = document.createElement("div");
  header.className = "inv-section-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(name) + ' <span class="inv-section-count">(' + parts.length + ')</span>' +
    (hasGroups ? '<button class="groups-btn' + (groupsActive ? ' active' : '') + '">◆ Groups</button>' : '');

  header.addEventListener("click", function (e) {
    if (e.target.closest(".groups-btn")) return;
    if (state.collapsedSections.has(name)) state.collapsedSections.delete(name);
    else state.collapsedSections.add(name);
    state._render();
  });
  var groupsBtn = header.querySelector(".groups-btn");
  if (groupsBtn) {
    groupsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (state.groupsSections.has(name)) state.groupsSections.delete(name);
      else state.groupsSections.add(name);
      state._render();
    });
  }
  section.appendChild(header);

  if (!isCollapsed) {
    if (groupsActive) {
      renderGroupedView(section, name, parts);
    } else if (activeVendorGroupScope() === "section" || activeVendorGroupScope() === "subsection") {
      // Flat section has no subsections, so subsection-scope and section-scope behave identically here.
      renderVendorPiles(section, parts, name);
    } else {
      var sorted = (activeSortScope() === "section" || activeSortScope() === "subsection") && state.sortColumn
        ? sortPartsBy(parts, state.sortColumn)
        : parts;
      for (var k = 0; k < sorted.length; k++) section.appendChild(createPartRow(sorted[k], name));
    }
  }
  state.body.appendChild(section);
}
