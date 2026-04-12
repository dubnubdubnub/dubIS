/* inventory-panel.js — Thin wiring for the inventory panel.
   Delegates to
   inventory-logic.js (pure functions) and inventory-renderer.js (DOM rendering). */

import { AppLog } from '../api.js';
import { showToast, escHtml } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, snapshotLinks, getThreshold } from '../store.js';
import { bomKey, invPartKey } from '../part-keys.js';
import { openAdjustModal, openPriceModal } from '../inventory-modals.js';
import { openEdit as openGenericEdit } from '../generic-parts-modal.js';

import {
  groupBySection,
  filterByQuery,
  filterByDistributor,
  countByDistributor,
} from './inventory-logic.js';

import {
  renderPartRowHtml,
} from './inventory-renderer.js';

import { renderBomComparison } from './inv-bom-view.js';
import { renderGroupedView } from './inv-groups-view.js';

import state from './inv-state.js';
import { setupEvents } from './inv-events.js';

// ── Section hierarchy (read once from store) ──

const SECTION_HIERARCHY = store.SECTION_HIERARCHY;
const FLAT_SECTIONS = store.FLAT_SECTIONS;

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
  const btns = state.distFilterBar.querySelectorAll(".dist-filter-btn");
  for (let i = 0; i < btns.length; i++) {
    btns[i].classList.toggle("active", btns[i].dataset.distributor === state.activeDistributor);
  }
  state.clearFilterBtn.disabled = (state.activeDistributor === null);
}

function updateDistCounts() {
  const counts = countByDistributor(store.inventory);
  const btns = state.distFilterBar.querySelectorAll(".dist-filter-btn");
  for (let i = 0; i < btns.length; i++) {
    const dist = btns[i].dataset.distributor;
    btns[i].textContent = dist.charAt(0).toUpperCase() + dist.slice(1) + " (" + counts[dist] + ")";
  }
}

// ── Reverse link helper ──

function createReverseLink(invItem) {
  const bomRow = store.links.linkingBomRow;
  if (!bomRow) return;
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(invItem);
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
    const matchedInvKeys = renderBomComparison(render, createReverseLink);
    renderRemainingInventory(matchedInvKeys, (state.searchInput.value || "").toLowerCase());
  } else {
    renderNormalInventory();
  }
}

// ── Normal mode: grouped by section ──

function renderNormalInventory() {
  const query = (state.searchInput.value || "").toLowerCase();
  const sections = groupBySection(store.inventory);

  for (let i = 0; i < SECTION_HIERARCHY.length; i++) {
    const entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      const filtered = filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributor);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, sections, query);
    }
  }
}

function renderHierarchySection(entry, sections, query) {
  const parentParts = filterByDistributor(filterByQuery(sections[entry.name] || [], query), state.activeDistributor);
  const childData = [];
  let totalCount = parentParts.length;
  for (let i = 0; i < entry.children.length; i++) {
    const fullKey = entry.name + " > " + entry.children[i];
    const filtered = filterByDistributor(filterByQuery(sections[fullKey] || [], query), state.activeDistributor);
    totalCount += filtered.length;
    childData.push({ name: entry.children[i], fullKey: fullKey, parts: filtered });
  }
  if (totalCount === 0) return;

  const container = document.createElement("div");
  container.className = "inv-section";

  const isParentCollapsed = state.collapsedSections.has(entry.name);
  const header = document.createElement("div");
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
    for (let j = 0; j < childData.length; j++) {
      if (childData[j].parts.length > 0) {
        renderSubSection(container, childData[j].name, childData[j].fullKey, childData[j].parts);
      }
    }
  }

  state.body.appendChild(container);
}

function renderSubSection(container, displayName, fullKey, parts) {
  const sub = document.createElement("div");
  sub.className = "inv-subsection";

  const isCollapsed = state.collapsedSections.has(fullKey);
  const hasGroups = store.genericParts && store.genericParts.length > 0;
  const groupsActive = state.groupsSections.has(fullKey);

  const header = document.createElement("div");
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
  const groupsBtn = header.querySelector(".groups-btn");
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
      renderGroupedView(sub, fullKey, parts, createPartRow, render);
    } else {
      for (let k = 0; k < parts.length; k++) {
        sub.appendChild(createPartRow(parts[k], fullKey));
      }
    }
  }

  container.appendChild(sub);
}

// ── Shared part row builder ──

function createPartRow(item, sectionKey) {
  const row = document.createElement("div");
  row.className = "inv-part-row";

  const isSource = store.links.linkingMode && store.links.linkingInvItem === item;
  const html = renderPartRowHtml(item, {
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
  const warnBtn = row.querySelector(".price-warn-btn");
  if (warnBtn) {
    warnBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openPriceModal(item);
    });
  }
  const distWarnBtn = row.querySelector(".no-dist-warn");
  if (distWarnBtn) {
    distWarnBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openAdjustModal(item);
    });
  }
  const linkBtnEl = row.querySelector(".link-btn");
  if (linkBtnEl) {
    linkBtnEl.addEventListener("click", function (e) {
      e.stopPropagation();
      store.links.setLinkingMode(true, item);
    });
  }
  const gpBadge = row.querySelector(".generic-group-badge");
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
  const otherParts = {};
  for (let i = 0; i < store.inventory.length; i++) {
    const item = store.inventory[i];
    const pk = invPartKey(item).toUpperCase();
    if (matchedInvKeys.has(pk)) continue;
    const sec = item.section || "Other";
    if (!otherParts[sec]) otherParts[sec] = [];
    otherParts[sec].push(item);
  }

  renderRemainingNormalSections(otherParts, query);
}

function renderRemainingNormalSections(otherParts, query) {
  const hasAny = FLAT_SECTIONS.some(function (s) { return !!otherParts[s]; });
  if (!hasAny) return;

  // Record position before rendering — if nothing is appended, skip divider
  const beforeCount = state.body.childNodes.length;

  for (let i = 0; i < SECTION_HIERARCHY.length; i++) {
    const entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      const filtered = filterByDistributor(filterByQuery(otherParts[entry.name] || [], query), state.activeDistributor);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, otherParts, query);
    }
  }

  // Only insert the divider if sections actually rendered
  if (state.body.childNodes.length > beforeCount) {
    const divider = document.createElement("div");
    divider.className = "inv-section-header inv-other-divider";
    divider.textContent = "Other Inventory";
    state.body.insertBefore(divider, state.body.childNodes[beforeCount]);
  }
}

// ── Flat section renderer ──

function renderSection(name, parts) {
  const section = document.createElement("div");
  section.className = "inv-section";

  const isCollapsed = state.collapsedSections.has(name);
  const hasGroups = store.genericParts && store.genericParts.length > 0;
  const groupsActive = state.groupsSections.has(name);

  const header = document.createElement("div");
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
  const groupsBtn = header.querySelector(".groups-btn");
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
      renderGroupedView(section, name, parts, createPartRow, render);
    } else {
      for (let k = 0; k < parts.length; k++) {
        section.appendChild(createPartRow(parts[k], name));
      }
    }
  }

  state.body.appendChild(section);
}
