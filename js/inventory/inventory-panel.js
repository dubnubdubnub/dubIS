/* inventory-panel.js — Thin wiring for the inventory panel.
   Absorbs bom-comparison.js responsibilities. Delegates to
   inventory-logic.js (pure functions) and inventory-renderer.js (DOM rendering). */

import { AppLog, api } from '../api.js';
import { EventBus, Events } from '../event-bus.js';
import { showToast, escHtml } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, snapshotLinks, getThreshold } from '../store.js';
import { bomKey, invPartKey, countStatuses } from '../part-keys.js';
import { openAdjustModal, openPriceModal } from '../inventory-modals.js';
import { openFlyout } from '../group-flyout/flyout-panel.js';

import {
  groupBySection,
  filterByQuery,
  filterByDistributor,
  countByDistributor,
  computeMatchedInvKeys,
  sortBomRows,
  buildRowMap,
  bomRowDisplayData,
  groupPartsByGeneric,
  computeFilterDimensions,
  filterMembersByChips,
} from './inventory-logic.js';

import {
  renderPartRowHtml,
  createBomRowElement,
  renderAltRows,
  renderMemberRows,
  renderFilterBarHtml,
  renderBomTableHeader,
} from './inventory-renderer.js';

import state from './inv-state.js';
import { setupEvents } from './inv-events.js';

// ── Section hierarchy (read once from store) ──

var SECTION_HIERARCHY = store.SECTION_HIERARCHY;
var FLAT_SECTIONS = store.FLAT_SECTIONS;

// ── Init ──

export function init() {
  state.body = document.getElementById("inventory-body");
  state.searchInput = document.getElementById("inv-search");
  state.clearFilterBtn = document.getElementById("clear-dist-filter");
  state.distFilterBar = document.getElementById("dist-filter-bar");

  setupEvents({ render: render, updateDistFilterUI: updateDistFilterUI });
}

// ── Distributor filter UI state ──

function updateDistFilterUI() {
  var btns = state.distFilterBar.querySelectorAll(".dist-filter-btn");
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.toggle("active", btns[i].dataset.distributor === state.activeDistributor);
  }
  state.clearFilterBtn.disabled = (state.activeDistributor === null);
}

function updateDistCounts() {
  var counts = countByDistributor(store.inventory);
  var btns = state.distFilterBar.querySelectorAll(".dist-filter-btn");
  for (var i = 0; i < btns.length; i++) {
    var dist = btns[i].dataset.distributor;
    var label = btns[i].querySelector(".dist-label");
    if (label) label.textContent = dist.charAt(0).toUpperCase() + dist.slice(1) + " (" + counts[dist] + ")";
  }
}

// ── Reverse link helper ──

function createReverseLink(invItem) {
  var bomRow = store.links.linkingBomRow;
  if (!bomRow) return;
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(invItem);
  if (!bk || !ipk) {
    showToast("Cannot create link \u2014 missing part key");
    return;
  }
  UndoRedo.save("links", snapshotLinks());
  store.links.addManualLink(bk, ipk);
  AppLog.info("Manual link: " + ipk + " \u2192 " + bk);
  store.links.setReverseLinkingMode(false);
  showToast("Linked " + ipk + " \u2192 " + bk);
}

// ── Main render ──

function render() {
  state.body.innerHTML = "";
  updateDistCounts();
  if (state.bomData) {
    var matchedInvKeys = renderBomComparison();
    renderRemainingInventory(matchedInvKeys, (state.searchInput.value || "").toLowerCase());
  } else {
    renderNormalInventory();
  }
}

// ── Normal mode: grouped by section ──

function renderNormalInventory() {
  var query = (state.searchInput.value || "").toLowerCase();
  var sections = groupBySection(store.inventory);

  for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
    var entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      var filtered = filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributor);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, sections, query);
    }
  }
}

function renderHierarchySection(entry, sections, query) {
  var parentParts = filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributor);
  var childData = [];
  var totalCount = parentParts.length;
  for (var i = 0; i < entry.children.length; i++) {
    var fullKey = entry.name + " > " + entry.children[i];
    var filtered = filterByDistributor(filterByQuery(sections[fullKey] || [], query), state.activeDistributor);
    totalCount += filtered.length;
    childData.push({ name: entry.children[i], fullKey: fullKey, parts: filtered });
  }
  if (totalCount === 0) return;

  var container = document.createElement("div");
  container.className = "inv-section";

  var isParentCollapsed = state.collapsedSections.has(entry.name);
  var header = document.createElement("div");
  header.className = "inv-parent-header" + (isParentCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(entry.name) + ' <span class="inv-section-count">(' + totalCount + ')</span>';
  header.addEventListener("click", function () {
    if (state.collapsedSections.has(entry.name)) state.collapsedSections.delete(entry.name);
    else state.collapsedSections.add(entry.name);
    render();
  });
  container.appendChild(header);

  if (!isParentCollapsed) {
    if (parentParts.length > 0) {
      renderSubSection(container, "Ungrouped", entry.name, parentParts);
    }
    for (var j = 0; j < childData.length; j++) {
      if (childData[j].parts.length > 0) {
        renderSubSection(container, childData[j].name, childData[j].fullKey, childData[j].parts);
      }
    }
  }

  state.body.appendChild(container);
}

function renderSubSection(container, displayName, fullKey, parts) {
  var sub = document.createElement("div");
  sub.className = "inv-subsection";

  var isCollapsed = state.collapsedSections.has(fullKey);
  var hasGroups = store.genericParts && store.genericParts.length > 0;
  var groupsActive = state.groupsSections.has(fullKey);

  var header = document.createElement("div");
  header.className = "inv-subsection-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(displayName) + ' <span class="inv-section-count">(' + parts.length + ')</span>' +
    (hasGroups ? '<button class="groups-btn' + (groupsActive ? ' active' : '') + '">\u25C6 Groups</button>' : '');

  // Collapse/expand on header click (but NOT on Groups button)
  header.addEventListener("click", function (e) {
    if (e.target.closest(".groups-btn")) return;
    if (state.collapsedSections.has(fullKey)) state.collapsedSections.delete(fullKey);
    else state.collapsedSections.add(fullKey);
    render();
  });

  // Groups button handler
  var groupsBtn = header.querySelector(".groups-btn");
  if (groupsBtn) {
    groupsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (state.groupsSections.has(fullKey)) state.groupsSections.delete(fullKey);
      else state.groupsSections.add(fullKey);
      render();
    });
  }
  sub.appendChild(header);

  if (!isCollapsed) {
    if (groupsActive) {
      renderGroupedView(sub, fullKey, parts);
    } else {
      for (var k = 0; k < parts.length; k++) {
        sub.appendChild(createPartRow(parts[k], fullKey));
      }
    }
  }

  container.appendChild(sub);
}

// ── Shared part row builder ──

function createPartRow(item, sectionKey) {
  var row = document.createElement("div");
  row.className = "inv-part-row";
  row.draggable = true;
  row.dataset.partId = invPartKey(item);

  var isSource = store.links.linkingMode && store.links.linkingInvItem === item;
  var html = renderPartRowHtml(item, {
    hideDescs: state.hideDescs,
    isBomMode: !!state.bomData,
    isLinkSource: isSource,
    isReverseTarget: false,
    sectionKey: sectionKey,
    threshold: getThreshold(sectionKey),
    genericParts: store.genericParts,
  });
  row.innerHTML = html;

  if (isSource) row.classList.add("linking-source");

  if (store.links.linkingMode && store.links.linkingBomRow) {
    row.classList.add("link-target");
    row.addEventListener("click", function () { createReverseLink(item); });
  }

  row.querySelector(".adj-btn").addEventListener("click", function (e) {
    e.stopPropagation();
    openAdjustModal(item);
  });
  var warnBtn = row.querySelector(".price-warn-btn");
  if (warnBtn) {
    warnBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openPriceModal(item);
    });
  }
  var distWarnBtn = row.querySelector(".no-dist-warn");
  if (distWarnBtn) {
    distWarnBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openAdjustModal(item);
    });
  }
  var linkBtnEl = row.querySelector(".link-btn");
  if (linkBtnEl) {
    linkBtnEl.addEventListener("click", function (e) {
      e.stopPropagation();
      store.links.setLinkingMode(true, item);
    });
  }
  var gpBadge = row.querySelector(".generic-group-badge");
  if (gpBadge) {
    gpBadge.addEventListener("click", function (e) {
      e.stopPropagation();
      openFlyout(gpBadge.dataset.genericId, gpBadge);
    });
  }

  return row;
}

// ── Remaining inventory (after BOM comparison) ──

function renderRemainingInventory(matchedInvKeys, query) {
  var otherParts = {};
  for (var i = 0; i < store.inventory.length; i++) {
    var item = store.inventory[i];
    var pk = invPartKey(item).toUpperCase();
    if (matchedInvKeys.has(pk)) continue;
    var sec = item.section || "Other";
    if (!otherParts[sec]) otherParts[sec] = [];
    otherParts[sec].push(item);
  }

  renderRemainingNormalSections(otherParts, query);
}

function renderRemainingNormalSections(otherParts, query) {
  var hasAny = FLAT_SECTIONS.some(function (s) { return !!otherParts[s]; });
  if (!hasAny) return;

  // Record position before rendering — if nothing is appended, skip divider
  var beforeCount = state.body.childNodes.length;

  for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
    var entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      var filtered = filterByDistributor(filterByQuery(otherParts[entry.name] || [], query), state.activeDistributor);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, otherParts, query);
    }
  }

  // Only insert the divider if sections actually rendered
  if (state.body.childNodes.length > beforeCount) {
    var divider = document.createElement("div");
    divider.className = "inv-section-header inv-other-divider";
    divider.textContent = "Other Inventory";
    state.body.insertBefore(divider, state.body.childNodes[beforeCount]);
  }
}

// ── Flat section renderer ──

function renderSection(name, parts) {
  var section = document.createElement("div");
  section.className = "inv-section";

  var isCollapsed = state.collapsedSections.has(name);
  var hasGroups = store.genericParts && store.genericParts.length > 0;
  var groupsActive = state.groupsSections.has(name);

  var header = document.createElement("div");
  header.className = "inv-section-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(name) + ' <span class="inv-section-count">(' + parts.length + ')</span>' +
    (hasGroups ? '<button class="groups-btn' + (groupsActive ? ' active' : '') + '">\u25C6 Groups</button>' : '');

  // Collapse/expand on header click (but NOT on Groups button)
  header.addEventListener("click", function (e) {
    if (e.target.closest(".groups-btn")) return;
    if (state.collapsedSections.has(name)) state.collapsedSections.delete(name);
    else state.collapsedSections.add(name);
    render();
  });

  // Groups button handler
  var groupsBtn = header.querySelector(".groups-btn");
  if (groupsBtn) {
    groupsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (state.groupsSections.has(name)) state.groupsSections.delete(name);
      else state.groupsSections.add(name);
      render();
    });
  }
  section.appendChild(header);

  if (!isCollapsed) {
    if (groupsActive) {
      renderGroupedView(section, name, parts);
    } else {
      for (var k = 0; k < parts.length; k++) {
        section.appendChild(createPartRow(parts[k], name));
      }
    }
  }

  state.body.appendChild(section);
}

// ── Groups mode rendering ──

function renderGroupedView(container, sectionKey, parts) {
  var result = groupPartsByGeneric(parts, store.genericParts || []);

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
        openFlyout(gpId, editBtn);
      });
    })(gp.generic_part_id);

    // In reverse-link mode, make group headers link-targets
    if (store.links.linkingMode && store.links.linkingBomRow) {
      headerDiv.classList.add("link-target");
    }

    container.appendChild(headerDiv);

    // Expanded: render filter row + member part rows
    if (isExpanded) {
      // Filter row
      var filterRowEl = renderFilterRow(gp, gpParts);
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

function renderFilterRow(gp, parts) {
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
    if (store.links.linkingMode && store.links.linkingBomRow) {
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

// ═══════════════════════════════════════════════════════
// ── BOM Comparison (absorbed from bom-comparison.js) ──
// ═══════════════════════════════════════════════════════

function renderBomComparison() {
  var query = (state.searchInput.value || "").toLowerCase();
  var rows = state.bomData.rows;
  var sortedRows = sortBomRows(rows);
  var c = countStatuses(rows);
  var linkingState = {
    linkingMode: store.links.linkingMode,
    linkingInvItem: store.links.linkingInvItem,
    linkingBomRow: store.links.linkingBomRow,
  };

  // Filter bar
  var filterBar = document.createElement("div");
  filterBar.className = "filter-bar";
  filterBar.innerHTML = renderFilterBarHtml(c, state.activeFilter);
  filterBar.querySelectorAll(".filter-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      state.activeFilter = btn.dataset.filter;
      render();
    });
  });
  state.body.appendChild(filterBar);

  // Build row lookup map for delegation
  state.rowMap = buildRowMap(sortedRows);

  // BOM matched section - table with full comparison
  var tableWrap = document.createElement("div");
  tableWrap.className = "table-wrap";
  var table = document.createElement("table");
  table.innerHTML = renderBomTableHeader();

  var tbody = document.createElement("tbody");
  for (var i = 0; i < sortedRows.length; i++) {
    var r = sortedRows[i];
    var d = bomRowDisplayData(r, query, state.activeFilter, state.expandedAlts, linkingState, state.expandedMembers);
    if (!d) continue;
    tbody.appendChild(createBomRowElement(d));
    if (d.showAlts) {
      var altElements = renderAltRows(r.alts, d.partKey);
      for (var j = 0; j < altElements.length; j++) {
        tbody.appendChild(altElements[j]);
      }
    }
    if (d.showMembers && d.genericMembers) {
      var resolvedId = r.inv ? invPartKey(r.inv) : "";
      var memberElements = renderMemberRows(d.genericMembers, d.partKey, resolvedId, d.genericPartName || "", store.inventory);
      for (var m = 0; m < memberElements.length; m++) {
        tbody.appendChild(memberElements[m]);
      }
    }
  }

  tbody.addEventListener("click", handleBomTableClick);

  table.appendChild(tbody);
  tableWrap.appendChild(table);
  state.body.appendChild(tableWrap);

  // Sticky horizontal scrollbar
  var stickyScroll = document.createElement("div");
  stickyScroll.className = "sticky-scrollbar";
  var stickyInner = document.createElement("div");
  stickyInner.style.height = "1px";
  stickyScroll.appendChild(stickyInner);
  state.body.appendChild(stickyScroll);

  function syncWidths() {
    stickyInner.style.width = table.scrollWidth + "px";
  }
  syncWidths();
  new ResizeObserver(syncWidths).observe(table);

  var syncing = false;
  stickyScroll.addEventListener("scroll", function () {
    if (syncing) return;
    syncing = true;
    tableWrap.scrollLeft = stickyScroll.scrollLeft;
    syncing = false;
  });
  tableWrap.addEventListener("scroll", function () {
    if (syncing) return;
    syncing = true;
    stickyScroll.scrollLeft = tableWrap.scrollLeft;
    syncing = false;
  });

  // Return matched inv keys
  return computeMatchedInvKeys(state.bomData);
}

// ── Auto-create group from BOM spec ──

/**
 * Infer part type from a BOM designator string (e.g. "C1,C2" → "capacitor").
 * Falls back to "other" when unknown.
 * @param {string} refs
 * @returns {string}
 */
function inferPartType(refs) {
  var first = (refs || "").trim().charAt(0).toUpperCase();
  if (first === "C") return "capacitor";
  if (first === "R") return "resistor";
  if (first === "L") return "inductor";
  return "other";
}

/**
 * Auto-create a generic group from BOM row data attributes and open the flyout.
 * Steps:
 *  1. Infer type from refs (C→capacitor, R→resistor, L→inductor)
 *  2. Extract spec from raw value + package string via backend
 *  3. Check if a matching group already exists (resolve_bom_spec)
 *  4. If found, open flyout for existing group
 *  5. If not found, create the group then open flyout
 * @param {HTMLElement} btn
 * @param {HTMLElement} row
 */
async function autoCreateGroupAndOpenFlyout(btn, row) {
  var bomValue = btn.dataset.bomValue || "";
  var bomPkg = btn.dataset.bomPkg || "";
  var bomRefs = btn.dataset.bomRefs || "";

  if (!bomValue) {
    AppLog.warn("Cannot auto-create group: no BOM value on button");
    return;
  }

  var partType = inferPartType(bomRefs);

  // Step 1: extract spec from raw value string
  var spec = await api("extract_spec_from_value", partType, bomValue, bomPkg);
  if (!spec) {
    AppLog.warn("Auto-create group: spec extraction failed for " + bomValue);
    return;
  }

  var numericValue = spec.value;

  // Step 2: check if a matching group already exists
  if (numericValue !== undefined && numericValue !== null) {
    var existing = await api("resolve_bom_spec", partType, numericValue, bomPkg);
    if (existing && existing.generic_part_id) {
      AppLog.info("Auto-create: found existing group " + existing.generic_part_id);
      openFlyout(existing.generic_part_id, row);
      return;
    }
  }

  // Step 3: create new generic group
  var name = bomValue + (bomPkg ? " " + bomPkg : "");
  var strictness = { required: ["value", "package"] };
  var result = await api("create_generic_part", name, partType, JSON.stringify(spec), JSON.stringify(strictness));
  if (!result || !result.generic_part_id) {
    AppLog.warn("Auto-create group: create_generic_part failed for " + name);
    return;
  }

  AppLog.info("Auto-created generic group: " + result.generic_part_id + " (" + name + ")");

  // Step 4: refresh store.genericParts
  var gps = await api("list_generic_parts");
  store.genericParts = Array.isArray(gps) ? gps : [];
  EventBus.emit(Events.GENERIC_PARTS_LOADED, store.genericParts);

  // Step 5: open flyout for the newly created group
  openFlyout(result.generic_part_id, row);
}

// ── Delegated tbody click handler ──

function handleBomTableClick(e) {
  // Alt badge toggle
  var badge = e.target.closest(".alt-badge");
  if (badge) {
    e.stopPropagation();
    var pk = badge.dataset.partKey;
    if (state.expandedAlts.has(pk)) state.expandedAlts.delete(pk);
    else state.expandedAlts.add(pk);
    render();
    return;
  }

  // Member badge toggle
  var memberBadge = e.target.closest(".member-badge");
  if (memberBadge) {
    e.stopPropagation();
    var mpk = memberBadge.dataset.partKey;
    if (state.expandedMembers.has(mpk)) state.expandedMembers.delete(mpk);
    else state.expandedMembers.add(mpk);
    render();
    return;
  }

  // Button clicks (both main rows and alt rows)
  var btn = e.target.closest("button");
  if (btn) {
    e.stopPropagation();
    var tr = btn.closest("tr");

    // Member row buttons
    if (tr.classList.contains("member-row")) {
      var memberPartId = tr.dataset.memberPartId;
      var memberParentKey = tr.dataset.memberFor;
      if (btn.classList.contains("use-member-btn")) {
        // Confirm match with this specific member
        var memberR = state.rowMap.get(memberParentKey);
        if (memberR) {
          UndoRedo.save("links", snapshotLinks());
          store.links.confirmMatch(bomKey(memberR.bom), memberPartId);
          AppLog.info("Generic member selected: " + memberParentKey + " \u2192 " + memberPartId);
          showToast("Confirmed " + memberParentKey + " \u2192 " + memberPartId);
          state.expandedMembers.delete(memberParentKey);
        }
      } else if (btn.classList.contains("adj-btn")) {
        // Find the inventory item for this member
        for (var mi = 0; mi < store.inventory.length; mi++) {
          var mItem = store.inventory[mi];
          if (invPartKey(mItem) === memberPartId || (mItem.lcsc && mItem.lcsc.toUpperCase() === memberPartId.toUpperCase()) || (mItem.mpn && mItem.mpn.toUpperCase() === memberPartId.toUpperCase())) {
            openAdjustModal(mItem);
            break;
          }
        }
      }
      return;
    }

    // Alt row buttons
    if (tr.classList.contains("alt-row")) {
      var parentKey = tr.dataset.altFor;
      var parentRow = state.rowMap.get(parentKey);
      var altKey = tr.dataset.invKey;
      var alt = parentRow && parentRow.alts
        ? parentRow.alts.find(function (a) { return invPartKey(a) === altKey; })
        : null;
      if (!alt) return;
      if (btn.classList.contains("adj-btn")) openAdjustModal(alt);
      else if (btn.classList.contains("swap-btn")) confirmAltMatch(parentRow, alt);
      return;
    }

    // Main row buttons
    var rowPk = tr.dataset.partKey;
    var r = state.rowMap.get(rowPk);
    if (!r) return;
    if (btn.classList.contains("group-flyout-btn")) {
      var gpId = btn.dataset.gpId;
      if (gpId) {
        openFlyout(gpId, /** @type {HTMLElement} */ (btn.closest("tr")));
      } else {
        autoCreateGroupAndOpenFlyout(btn, /** @type {HTMLElement} */ (btn.closest("tr")));
      }
      return;
    }
    if (btn.classList.contains("confirm-btn")) confirmMatch(r);
    else if (btn.classList.contains("unconfirm-btn")) unconfirmMatch(r);
    else if (btn.classList.contains("adj-btn")) openAdjustModal(r.inv);
    else if (btn.classList.contains("link-btn")) {
      if (r.inv) store.links.setLinkingMode(true, r.inv);
      else if (r.effectiveStatus === "missing") store.links.setReverseLinkingMode(true, r);
    }
    return;
  }

  // Reverse linking: row click on link-target
  var linkTr = e.target.closest("tr.link-target");
  if (linkTr && !linkTr.classList.contains("alt-row")) {
    var linkPk = linkTr.dataset.partKey;
    var linkR = state.rowMap.get(linkPk);
    if (linkR && linkR.inv) createReverseLink(linkR.inv);
  }
}

// ── Confirm Match Functions ──

function confirmMatch(bomRow) {
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(bomRow.inv);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed: " + bk + " \u2192 " + ipk);
  showToast("Confirmed " + bk);
}

function unconfirmMatch(bomRow) {
  var bk = bomKey(bomRow.bom);
  if (!bk) { AppLog.warn("Cannot unconfirm: missing BOM key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.unconfirmMatch(bk);
  AppLog.info("Unconfirmed: " + bk);
  showToast("Unconfirmed " + bk);
}

function confirmAltMatch(bomRow, altInvItem) {
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(altInvItem);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm alt: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed alt: " + bk + " \u2192 " + ipk);
  showToast("Confirmed " + bk + " \u2192 " + ipk);
}
