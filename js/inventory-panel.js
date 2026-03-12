/* inventory-panel.js — Middle panel: inventory viewer + BOM comparison overlay.
   Normal mode: parts grouped by section with search.
   BOM mode: delegates to bom-comparison.js, then renders remaining inventory. */

import { EventBus, Events } from './event-bus.js';
import { AppLog } from './api.js';
import { showToast, escHtml, stockValueColor } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { App, snapshotLinks, getThreshold } from './store.js';
import { bomKey, invPartKey } from './part-keys.js';
import { openAdjustModal, openPriceModal } from './inventory-modals.js';
import {
  bomData, initBomComparison, setBomData, clearBomState,
  renderBomComparison,
} from './bom-comparison.js';

const body = document.getElementById("inventory-body");
const searchInput = document.getElementById("inv-search");
let collapsedSections = new Set();

// Hide descriptions when panel is too narrow for readable text
// Fixed children sum to ~511px (100 ids + 160 mpn + 70 value + 60 qty + 45 btn + 40 gaps + 36 pad)
// 680px gives descriptions ~170px minimum usable width
const DESC_HIDE_WIDTH = 680;
// Start hidden — ResizeObserver will show them once panel is confirmed wide enough.
// This avoids a timing race where body.offsetWidth is 0 at script execution time
// and the first render() fires before the observer's initial callback.
let hideDescs = true;
new ResizeObserver(([entry]) => {
  const narrow = entry.contentRect.width < DESC_HIDE_WIDTH;
  if (narrow !== hideDescs) { hideDescs = narrow; render(); }
}).observe(body);

// Log app dimensions on resize
window.addEventListener("resize", () => {
  AppLog.info("Window: " + window.innerWidth + "×" + window.innerHeight + "  inv-body: " + body.offsetWidth + "×" + body.offsetHeight);
});

const SECTION_HIERARCHY = App.SECTION_HIERARCHY;
const FLAT_SECTIONS = App.FLAT_SECTIONS;

// ── Wire up bom-comparison dependencies ──

initBomComparison({
  render,
  openAdjustModal,
  openPriceModal,
  createReverseLink,
  renderNormalSections: renderRemainingNormalSections,
  filterByQuery,
});

// ── Reverse link helper (BOM missing row → inventory part) ──

function createReverseLink(invItem) {
  const bomRow = App.links.linkingBomRow;
  if (!bomRow) return;
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(invItem);
  if (!bk || !ipk) {
    showToast("Cannot create link — missing part key");
    return;
  }
  UndoRedo.save("links", snapshotLinks());
  App.links.addManualLink(bk, ipk);
  AppLog.info("Manual link: " + ipk + " → " + bk);
  App.links.setReverseLinkingMode(false);
  showToast("Linked " + ipk + " → " + bk);
}

// ── Main render ──

function render() {
  body.innerHTML = "";
  if (bomData) {
    const matchedInvKeys = renderBomComparison(body, searchInput);
    renderRemainingInventory(matchedInvKeys, (searchInput.value || "").toLowerCase());
  } else {
    renderNormalInventory();
  }
}

// ── Normal mode: grouped by section ──

function renderNormalInventory() {
  const query = (searchInput.value || "").toLowerCase();
  const sections = {};
  App.inventory.forEach(item => {
    const sec = item.section || "Other";
    (sections[sec] = sections[sec] || []).push(item);
  });

  SECTION_HIERARCHY.forEach(entry => {
    if (!entry.children) {
      // Flat section — unchanged
      const filtered = filterByQuery(sections[entry.name] || [], query);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      // Parent with subcategories
      renderHierarchySection(entry, sections, query);
    }
  });
}

function renderHierarchySection(entry, sections, query) {
  // Gather all parts: bare parent + compound children
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
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(entry.name) + ' <span class="inv-section-count">(' + totalCount + ')</span>';
  header.addEventListener("click", function () {
    if (collapsedSections.has(entry.name)) collapsedSections.delete(entry.name);
    else collapsedSections.add(entry.name);
    render();
  });
  container.appendChild(header);

  if (!isParentCollapsed) {
    // Ungrouped parts at parent level
    if (parentParts.length > 0) {
      renderSubSection(container, "Ungrouped", entry.name, parentParts);
    }
    // Subcategory sections
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
  header.innerHTML = '<span class="chevron">▾</span> ' + escHtml(displayName) + ' <span class="inv-section-count">(' + parts.length + ')</span>';
  header.addEventListener("click", function () {
    if (collapsedSections.has(fullKey)) collapsedSections.delete(fullKey);
    else collapsedSections.add(fullKey);
    render();
  });
  sub.appendChild(header);

  if (!isCollapsed) {
    parts.forEach(function (item) {
      sub.appendChild(createPartRow(item, fullKey));
    });
  }

  container.appendChild(sub);
}

// ── Shared part row builder (used by both renderSubSection and renderSection) ──

function createPartRow(item, sectionKey) {
  var row = document.createElement("div");
  row.className = "inv-part-row";

  var displayMpn = item.mpn || "";
  var displayDesc = item.description || "";

  var stockValue = item.qty * (item.unit_price || 0);
  var qtyColor = stockValueColor(stockValue, getThreshold(sectionKey));
  var showPriceWarn = item.qty > 0 && !(item.unit_price > 0);

  var isSource = App.links.linkingMode && App.links.linkingInvItem === item;
  var linkBtnStr = bomData ? '<button class="link-btn' + (isSource ? ' active' : '') + '" title="Link to missing BOM row">Link</button>' : '';
  var valueStr = stockValue > 0 ? "$" + stockValue.toFixed(2) : "—";

  var partIdsHtml = '<span class="part-ids">';
  if (item.lcsc) partIdsHtml += '<span class="part-id-lcsc" data-lcsc="' + escHtml(item.lcsc) + '"><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(item.lcsc) + '</span>';
  if (item.digikey) partIdsHtml += '<span class="part-id-digikey" data-digikey="' + escHtml(item.digikey) + '"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(item.digikey) + '</span>';
  if (!item.lcsc && !item.digikey) partIdsHtml += '<span></span>';
  partIdsHtml += '</span>';

  row.innerHTML =
    partIdsHtml +
    '<span class="part-mpn" title="' + escHtml(displayMpn) + '">' + escHtml(displayMpn) + '</span>' +
    '<span class="part-value">' + valueStr + '</span>' +
    '<span class="part-qty" style="color:' + qtyColor + '">' + (showPriceWarn ? '<button class="price-warn-btn" title="No price data — click to set">⚠</button>' : '') + item.qty + '</span>' +
    (hideDescs ? '' : '<span class="part-desc"><span class="part-desc-inner" title="' + escHtml(displayDesc) + '">' + escHtml(displayDesc) + '</span></span>') +
    '<button class="adj-btn" title="Adjust qty">Adjust</button>' +
    linkBtnStr;

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
  const otherParts = {};
  App.inventory.forEach(item => {
    const pk = invPartKey(item).toUpperCase();
    if (matchedInvKeys.has(pk)) return;
    const sec = item.section || "Other";
    (otherParts[sec] = otherParts[sec] || []).push(item);
  });

  renderRemainingNormalSections(otherParts, query);
}

function renderRemainingNormalSections(otherParts, query) {
  const hasAny = FLAT_SECTIONS.some(s => otherParts[s]);
  if (hasAny) {
    const divider = document.createElement("div");
    divider.className = "inv-section-header";
    divider.style.borderTop = "2px solid #30363d";
    divider.style.marginTop = "4px";
    divider.style.color = "#484f58";
    divider.style.cursor = "default";
    divider.textContent = "Other Inventory";
    body.appendChild(divider);

    SECTION_HIERARCHY.forEach(entry => {
      if (!entry.children) {
        const filtered = filterByQuery(otherParts[entry.name] || [], query);
        if (filtered.length > 0) renderSection(entry.name, filtered);
      } else {
        renderHierarchySection(entry, otherParts, query);
      }
    });
  }
}

// ── Shared helpers ──

function filterByQuery(parts, query) {
  if (!query) return parts;
  return parts.filter(item => {
    const text = [item.lcsc, item.mpn, item.description, item.manufacturer, item.package, item.digikey]
      .join(" ").toLowerCase();
    return text.includes(query);
  });
}

function renderSection(name, parts) {
  const section = document.createElement("div");
  section.className = "inv-section";

  const isCollapsed = collapsedSections.has(name);
  const header = document.createElement("div");
  header.className = "inv-section-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = `<span class="chevron">▾</span> ${escHtml(name)} <span class="inv-section-count">(${parts.length})</span>`;
  header.addEventListener("click", () => {
    if (collapsedSections.has(name)) collapsedSections.delete(name);
    else collapsedSections.add(name);
    render();
  });
  section.appendChild(header);

  if (!isCollapsed) {
    parts.forEach(item => {
      section.appendChild(createPartRow(item, name));
    });
  }

  body.appendChild(section);
}

// ── Search ──
function debounce(fn, ms) {
  let timer;
  return function(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}
searchInput.addEventListener("input", debounce(() => render(), 150));

// ── Event listeners ──
EventBus.on(Events.INVENTORY_LOADED, () => render());
EventBus.on(Events.INVENTORY_UPDATED, () => render());
EventBus.on(Events.PREFS_CHANGED, () => render());

EventBus.on(Events.BOM_LOADED, (data) => {
  setBomData(data);
  render();
});

EventBus.on(Events.BOM_CLEARED, () => {
  clearBomState();
  App.links.clearAll();
  render();
});

EventBus.on(Events.LINKING_MODE, () => render());

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && App.links.linkingMode) {
    if (App.links.linkingBomRow) App.links.setReverseLinkingMode(false);
    else App.links.setLinkingMode(false);
  }
});
