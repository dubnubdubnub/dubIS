/* inventory/inv-bom-mode.js — BOM-comparison view and remaining inventory rendering.
   buildNearMissMap, renderBomComparison,
   renderRemainingInventory, renderRemainingNormalSections. */

import { store } from '../store.js';
import { invPartKey, countStatuses } from '../part-keys.js';
import {
  sortBomRows,
  buildRowMap,
  bomRowDisplayData,
  computeMatchedInvKeys,
  filterByQuery,
  filterByDistributor,
} from './inventory-logic.js';
import {
  createBomRowElement,
  renderAltRows,
  renderMemberRows,
  renderFilterBarHtml,
  renderBomTableHeader,
} from './inventory-renderer.js';
import { isFlyoutDragActive } from './inv-events.js';
import state from './inv-state.js';
import { handleBomTableClick } from './inv-mutations.js';
import { renderSection, renderHierarchySection } from './inv-render.js';

// ── Section hierarchy (read once from store) ──

var SECTION_HIERARCHY = store.SECTION_HIERARCHY;
var FLAT_SECTIONS = store.FLAT_SECTIONS;

// ── Near-miss map builder ──

export function buildNearMissMap() {
  var map = new Map();
  var list = store.bomFootprintNearMisses || [];
  for (var i = 0; i < list.length; i++) {
    var nm = list[i];
    if (!nm.inv) continue;
    var key = invPartKey(nm.inv).toUpperCase();
    // Keep first occurrence per inventory key; subsequent near-misses for the same
    // inventory item are tolerated but not shown in the badge.
    if (!map.has(key)) map.set(key, nm);
  }
  return map;
}

// ── BOM Comparison ──

export function renderBomComparison() {
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
      state._render();
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
  var flyoutActive = isFlyoutDragActive();
  for (var i = 0; i < sortedRows.length; i++) {
    var r = sortedRows[i];
    var d = bomRowDisplayData(r, query, state.activeFilter, state.expandedAlts, linkingState, state.expandedMembers);
    if (!d) continue;
    var bomTr = createBomRowElement(d);
    bomTr.draggable = flyoutActive;
    tbody.appendChild(bomTr);
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

// ── Remaining inventory (after BOM comparison) ──

export function renderRemainingInventory(matchedInvKeys, query) {
  state.nearMissMap = buildNearMissMap();
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

export function renderRemainingNormalSections(otherParts, query) {
  var hasAny = FLAT_SECTIONS.some(function (s) { return !!otherParts[s]; });
  if (!hasAny) return;

  // Record position before rendering — if nothing is appended, skip divider
  var beforeCount = state.body.childNodes.length;

  for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
    var entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      var filtered = filterByDistributor(filterByQuery(otherParts[entry.name] || [], query), state.activeDistributors);
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
