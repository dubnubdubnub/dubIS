/* inventory-panel.js — Thin wiring for the inventory panel.
   Absorbs bom-comparison.js responsibilities. Delegates to
   inventory-logic.js (pure functions) and inventory-renderer.js (DOM rendering). */

import { AppLog } from '../api.js';
import { showToast, escHtml } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { App, store, snapshotLinks, getThreshold } from '../store.js';
import { bomKey, invPartKey, countStatuses } from '../part-keys.js';
import { openAdjustModal, openPriceModal } from '../inventory-modals.js';
import { openCreate as openGenericCreate, openEdit as openGenericEdit } from '../generic-parts-modal.js';

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

  setupEvents({ render: render });
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
  state.body.innerHTML = "";
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
  var header = document.createElement("div");
  header.className = "inv-subsection-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(displayName) + ' <span class="inv-section-count">(' + parts.length + ')</span>';
  header.addEventListener("click", function () {
    if (state.collapsedSections.has(fullKey)) state.collapsedSections.delete(fullKey);
    else state.collapsedSections.add(fullKey);
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
    hideDescs: state.hideDescs,
    isBomMode: !!state.bomData,
    isLinkSource: isSource,
    isReverseTarget: false,
    sectionKey: sectionKey,
    threshold: getThreshold(sectionKey),
    genericParts: App.genericParts,
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
  var gpBadge = row.querySelector(".generic-group-badge");
  if (gpBadge) {
    gpBadge.addEventListener("click", function (e) {
      e.stopPropagation();
      openGenericEdit(gpBadge.dataset.genericId);
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
  if (hasAny) {
    var divider = document.createElement("div");
    divider.className = "inv-section-header inv-other-divider";
    divider.textContent = "Other Inventory";
    state.body.appendChild(divider);

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

  var isCollapsed = state.collapsedSections.has(name);
  var header = document.createElement("div");
  header.className = "inv-section-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(name) + ' <span class="inv-section-count">(' + parts.length + ')</span>';
  header.addEventListener("click", function () {
    if (state.collapsedSections.has(name)) state.collapsedSections.delete(name);
    else state.collapsedSections.add(name);
    render();
  });
  section.appendChild(header);

  if (!isCollapsed) {
    for (var k = 0; k < parts.length; k++) {
      section.appendChild(createPartRow(parts[k], name));
    }
  }

  state.body.appendChild(section);
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
    linkingMode: App.links.linkingMode,
    linkingInvItem: App.links.linkingInvItem,
    linkingBomRow: App.links.linkingBomRow,
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
      var memberElements = renderMemberRows(d.genericMembers, d.partKey, resolvedId, d.genericPartName || "", App.inventory);
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
        var memberR = rowMap.get(memberParentKey);
        if (memberR) {
          UndoRedo.save("links", snapshotLinks());
          App.links.confirmMatch(bomKey(memberR.bom), memberPartId);
          AppLog.info("Generic member selected: " + memberParentKey + " \u2192 " + memberPartId);
          showToast("Confirmed " + memberParentKey + " \u2192 " + memberPartId);
          state.expandedMembers.delete(memberParentKey);
        }
      } else if (btn.classList.contains("adj-btn")) {
        // Find the inventory item for this member
        for (var mi = 0; mi < App.inventory.length; mi++) {
          var mItem = App.inventory[mi];
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
    if (btn.classList.contains("create-generic-btn")) {
      var typeMap = { C: "capacitor", R: "resistor", L: "inductor" };
      var refChar = (btn.dataset.bomRefs || "").trim().charAt(0).toUpperCase();
      openGenericCreate(null, {
        type: typeMap[refChar] || undefined,
        value: btn.dataset.bomValue || undefined,
        package: btn.dataset.bomPkg || undefined,
      });
      return;
    }
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
