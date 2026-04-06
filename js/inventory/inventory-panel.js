/* inventory-panel.js — Thin wiring for the inventory panel.
   Absorbs bom-comparison.js responsibilities. Delegates to
   inventory-logic.js (pure functions) and inventory-renderer.js (DOM rendering). */

import { EventBus, Events } from '../event-bus.js';
import { AppLog } from '../api.js';
import { showToast, escHtml } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { App, snapshotLinks, getThreshold } from '../store.js';
import { bomKey, invPartKey, countStatuses } from '../part-keys.js';
import { openAdjustModal, openPriceModal } from '../inventory-modals.js';

import {
  groupBySection,
  filterByQuery,
  computeMatchedInvKeys,
  sortBomRows,
  buildRowMap,
  bomRowDisplayData,
} from './inventory-logic.js';

import {
  renderPartRowHtml,
  createBomRowElement,
  renderAltRows,
  renderFilterBarHtml,
  renderBomTableHeader,
} from './inventory-renderer.js';

// ── DOM references ──

var body = document.getElementById("inventory-body");
var searchInput = document.getElementById("inv-search");

// ── Panel state ──

var collapsedSections = new Set();
var bomData = null;        // { rows, fileName, multiplier }
var activeFilter = "all";
var expandedAlts = new Set();
var rowMap = new Map();    // partKey -> r, rebuilt each render

// Hide descriptions when panel is too narrow for readable text
var DESC_HIDE_WIDTH = 680;
var hideDescs = true;

export function init() {
  // ── ResizeObserver for description hiding ──
  new ResizeObserver(function (entries) {
    var narrow = entries[0].contentRect.width < DESC_HIDE_WIDTH;
    if (narrow !== hideDescs) { hideDescs = narrow; render(); }
  }).observe(body);

  // Log app dimensions on resize
  window.addEventListener("resize", function () {
    AppLog.info("Window: " + window.innerWidth + "\u00D7" + window.innerHeight + "  inv-body: " + body.offsetWidth + "\u00D7" + body.offsetHeight);
  });

  // ── Search ──
  var searchTimer;
  searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { render(); }, 150);
  });

  // ── Event listeners ──
  EventBus.on(Events.INVENTORY_LOADED, function () { render(); });
  EventBus.on(Events.INVENTORY_UPDATED, function () { render(); });
  EventBus.on(Events.PREFS_CHANGED, function () { render(); });

  EventBus.on(Events.BOM_LOADED, function (data) {
    bomData = data;
    render();
  });

  EventBus.on(Events.BOM_CLEARED, function () {
    bomData = null;
    activeFilter = "all";
    expandedAlts = new Set();
    App.links.clearAll();
    render();
  });

  EventBus.on(Events.LINKING_MODE, function () { render(); });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && App.links.linkingMode) {
      if (App.links.linkingBomRow) App.links.setReverseLinkingMode(false);
      else App.links.setLinkingMode(false);
    }
  });
}

// ── Reverse link helper ──

function createReverseLink(invItem) {
  var bomRow = App.links.linkingBomRow;
  if (!bomRow) return;
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(invItem);
  if (!bk || !ipk) {
    showToast("Cannot create link \u2014 missing part key");
    return;
  }
  UndoRedo.save("links", snapshotLinks());
  App.links.addManualLink(bk, ipk);
  AppLog.info("Manual link: " + ipk + " \u2192 " + bk);
  App.links.setReverseLinkingMode(false);
  showToast("Linked " + ipk + " \u2192 " + bk);
}

// ── Main render ──

function render() {
  body.innerHTML = "";
  if (bomData) {
    var matchedInvKeys = renderBomComparison();
    renderRemainingInventory(matchedInvKeys, (searchInput.value || "").toLowerCase());
  } else {
    renderNormalInventory();
  }
}

// ── Normal mode: grouped by section ──

var SECTION_HIERARCHY = App.SECTION_HIERARCHY;
var FLAT_SECTIONS = App.FLAT_SECTIONS;

function renderNormalInventory() {
  var query = (searchInput.value || "").toLowerCase();
  var sections = groupBySection(App.inventory);

  for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
    var entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      var filtered = filterByQuery(sections[entry.name] || [], query);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, sections, query);
    }
  }
}

function renderHierarchySection(entry, sections, query) {
  var parentParts = filterByQuery(sections[entry.name] || [], query);
  var childData = [];
  var totalCount = parentParts.length;
  for (var i = 0; i < entry.children.length; i++) {
    var fullKey = entry.name + " > " + entry.children[i];
    var filtered = filterByQuery(sections[fullKey] || [], query);
    totalCount += filtered.length;
    childData.push({ name: entry.children[i], fullKey: fullKey, parts: filtered });
  }
  if (totalCount === 0) return;

  var container = document.createElement("div");
  container.className = "inv-section";

  var isParentCollapsed = collapsedSections.has(entry.name);
  var header = document.createElement("div");
  header.className = "inv-parent-header" + (isParentCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(entry.name) + ' <span class="inv-section-count">(' + totalCount + ')</span>';
  header.addEventListener("click", function () {
    if (collapsedSections.has(entry.name)) collapsedSections.delete(entry.name);
    else collapsedSections.add(entry.name);
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

  body.appendChild(container);
}

function renderSubSection(container, displayName, fullKey, parts) {
  var sub = document.createElement("div");
  sub.className = "inv-subsection";

  var isCollapsed = collapsedSections.has(fullKey);
  var header = document.createElement("div");
  header.className = "inv-subsection-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(displayName) + ' <span class="inv-section-count">(' + parts.length + ')</span>';
  header.addEventListener("click", function () {
    if (collapsedSections.has(fullKey)) collapsedSections.delete(fullKey);
    else collapsedSections.add(fullKey);
    render();
  });
  sub.appendChild(header);

  if (!isCollapsed) {
    for (var k = 0; k < parts.length; k++) {
      sub.appendChild(createPartRow(parts[k], fullKey));
    }
  }

  container.appendChild(sub);
}

// ── Shared part row builder ──

function createPartRow(item, sectionKey) {
  var row = document.createElement("div");
  row.className = "inv-part-row";

  var isSource = App.links.linkingMode && App.links.linkingInvItem === item;
  var html = renderPartRowHtml(item, {
    hideDescs: hideDescs,
    isBomMode: !!bomData,
    isLinkSource: isSource,
    isReverseTarget: false,
    sectionKey: sectionKey,
    threshold: getThreshold(sectionKey),
  });
  row.innerHTML = html;

  if (isSource) row.classList.add("linking-source");

  if (App.links.linkingMode && App.links.linkingBomRow) {
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
      App.links.setLinkingMode(true, item);
    });
  }

  return row;
}

// ── Remaining inventory (after BOM comparison) ──

function renderRemainingInventory(matchedInvKeys, query) {
  var otherParts = {};
  for (var i = 0; i < App.inventory.length; i++) {
    var item = App.inventory[i];
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
  if (hasAny) {
    var divider = document.createElement("div");
    divider.className = "inv-section-header inv-other-divider";
    divider.textContent = "Other Inventory";
    body.appendChild(divider);

    for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
      var entry = SECTION_HIERARCHY[i];
      if (!entry.children) {
        var filtered = filterByQuery(otherParts[entry.name] || [], query);
        if (filtered.length > 0) renderSection(entry.name, filtered);
      } else {
        renderHierarchySection(entry, otherParts, query);
      }
    }
  }
}

// ── Flat section renderer ──

function renderSection(name, parts) {
  var section = document.createElement("div");
  section.className = "inv-section";

  var isCollapsed = collapsedSections.has(name);
  var header = document.createElement("div");
  header.className = "inv-section-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(name) + ' <span class="inv-section-count">(' + parts.length + ')</span>';
  header.addEventListener("click", function () {
    if (collapsedSections.has(name)) collapsedSections.delete(name);
    else collapsedSections.add(name);
    render();
  });
  section.appendChild(header);

  if (!isCollapsed) {
    for (var k = 0; k < parts.length; k++) {
      section.appendChild(createPartRow(parts[k], name));
    }
  }

  body.appendChild(section);
}

// ═══════════════════════════════════════════════════════
// ── BOM Comparison (absorbed from bom-comparison.js) ──
// ═══════════════════════════════════════════════════════

function renderBomComparison() {
  var query = (searchInput.value || "").toLowerCase();
  var rows = bomData.rows;
  var sortedRows = sortBomRows(rows);
  var c = countStatuses(rows);
  var linkingState = {
    linkingMode: App.links.linkingMode,
    linkingInvItem: App.links.linkingInvItem,
    linkingBomRow: App.links.linkingBomRow,
  };

  // Filter bar
  var filterBar = document.createElement("div");
  filterBar.className = "filter-bar";
  filterBar.innerHTML = renderFilterBarHtml(c, activeFilter);
  filterBar.querySelectorAll(".filter-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      activeFilter = btn.dataset.filter;
      render();
    });
  });
  body.appendChild(filterBar);

  // Build row lookup map for delegation
  rowMap = buildRowMap(sortedRows);

  // BOM matched section - table with full comparison
  var tableWrap = document.createElement("div");
  tableWrap.className = "table-wrap";
  var table = document.createElement("table");
  table.innerHTML = renderBomTableHeader();

  var tbody = document.createElement("tbody");
  for (var i = 0; i < sortedRows.length; i++) {
    var r = sortedRows[i];
    var d = bomRowDisplayData(r, query, activeFilter, expandedAlts, linkingState);
    if (!d) continue;
    tbody.appendChild(createBomRowElement(d));
    if (d.showAlts) {
      var altElements = renderAltRows(r.alts, d.partKey);
      for (var j = 0; j < altElements.length; j++) {
        tbody.appendChild(altElements[j]);
      }
    }
  }

  tbody.addEventListener("click", handleBomTableClick);

  table.appendChild(tbody);
  tableWrap.appendChild(table);
  body.appendChild(tableWrap);

  // Sticky horizontal scrollbar
  var stickyScroll = document.createElement("div");
  stickyScroll.className = "sticky-scrollbar";
  var stickyInner = document.createElement("div");
  stickyInner.style.height = "1px";
  stickyScroll.appendChild(stickyInner);
  body.appendChild(stickyScroll);

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
  return computeMatchedInvKeys(bomData);
}

// ── Delegated tbody click handler ──

function handleBomTableClick(e) {
  // Alt badge toggle
  var badge = e.target.closest(".alt-badge");
  if (badge) {
    e.stopPropagation();
    var pk = badge.dataset.partKey;
    if (expandedAlts.has(pk)) expandedAlts.delete(pk);
    else expandedAlts.add(pk);
    render();
    return;
  }

  // Button clicks (both main rows and alt rows)
  var btn = e.target.closest("button");
  if (btn) {
    e.stopPropagation();
    var tr = btn.closest("tr");

    // Alt row buttons
    if (tr.classList.contains("alt-row")) {
      var parentKey = tr.dataset.altFor;
      var parentRow = rowMap.get(parentKey);
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
    var r = rowMap.get(rowPk);
    if (!r) return;
    if (btn.classList.contains("confirm-btn")) confirmMatch(r);
    else if (btn.classList.contains("unconfirm-btn")) unconfirmMatch(r);
    else if (btn.classList.contains("adj-btn")) openAdjustModal(r.inv);
    else if (btn.classList.contains("link-btn")) {
      if (r.inv) App.links.setLinkingMode(true, r.inv);
      else if (r.effectiveStatus === "missing") App.links.setReverseLinkingMode(true, r);
    }
    return;
  }

  // Reverse linking: row click on link-target
  var linkTr = e.target.closest("tr.link-target");
  if (linkTr && !linkTr.classList.contains("alt-row")) {
    var linkPk = linkTr.dataset.partKey;
    var linkR = rowMap.get(linkPk);
    if (linkR && linkR.inv) createReverseLink(linkR.inv);
  }
}

// ── Confirm Match Functions ──

function confirmMatch(bomRow) {
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(bomRow.inv);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  App.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed: " + bk + " \u2192 " + ipk);
  showToast("Confirmed " + bk);
}

function unconfirmMatch(bomRow) {
  var bk = bomKey(bomRow.bom);
  if (!bk) { AppLog.warn("Cannot unconfirm: missing BOM key"); return; }
  UndoRedo.save("links", snapshotLinks());
  App.links.unconfirmMatch(bk);
  AppLog.info("Unconfirmed: " + bk);
  showToast("Unconfirmed " + bk);
}

function confirmAltMatch(bomRow, altInvItem) {
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(altInvItem);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm alt: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  App.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed alt: " + bk + " \u2192 " + ipk);
  showToast("Confirmed " + bk + " \u2192 " + ipk);
}
