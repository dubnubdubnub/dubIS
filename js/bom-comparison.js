/* bom-comparison.js — BOM comparison table in the inventory panel.
   Renders matched/unmatched parts, filter bar, alt rows, and delegated click handling.
   Extracted from inventory-panel.js for focused maintainability. */

import { escHtml, showToast } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { App, snapshotLinks } from './store.js';
import { bomKey, invPartKey, countStatuses, STATUS_ROW_CLASS, colorizeRefs } from './part-keys.js';
import { bomRowDisplayData } from './bom-row-data.js';
import { AppLog } from './api.js';

const BOM_STATUS_SORT_ORDER = {
  missing: 0, "manual-short": 0.4, manual: 0.5,
  "confirmed-short": 0.7, confirmed: 0.75,
  possible: 1, short: 2, ok: 3, dnp: 4,
};

// ── BOM comparison state ──
export let bomData = null;        // { rows, fileName, multiplier }
export let activeFilter = "all";
export let expandedAlts = new Set();
export let rowMap = new Map();    // partKey → r, rebuilt each render

// Callbacks set by inventory-panel via initBomComparison
let _deps = {
  render: null,
  openAdjustModal: null,
  openPriceModal: null,
  createReverseLink: null,
  renderNormalSections: null,
  filterByQuery: null,
};

export function initBomComparison(deps) {
  _deps = { ..._deps, ...deps };
}

export function setBomData(data) {
  bomData = data;
}

export function clearBomState() {
  bomData = null;
  activeFilter = "all";
  expandedAlts = new Set();
}

// ── Filter bar ──

function renderFilterBar(body, c) {
  const filterBar = document.createElement("div");
  filterBar.className = "filter-bar";
  filterBar.innerHTML = `
    <button class="filter-btn${activeFilter === "all" ? " active" : ""}" data-filter="all">All (${c.total})</button>
    ${c.manual > 0 ? `<button class="filter-btn${activeFilter === "manual" ? " active" : ""}" data-filter="manual">Manual (${c.manual})</button>` : ''}
    ${c.confirmed > 0 ? `<button class="filter-btn${activeFilter === "confirmed" ? " active" : ""}" data-filter="confirmed">Confirmed (${c.confirmed})</button>` : ''}
    <button class="filter-btn${activeFilter === "ok" ? " active" : ""}" data-filter="ok">In Stock (${c.ok})</button>
    <button class="filter-btn${activeFilter === "short" ? " active" : ""}" data-filter="short">Short (${c.short})</button>
    <button class="filter-btn${activeFilter === "possible" ? " active" : ""}" data-filter="possible">Possible (${c.possible})</button>
    <button class="filter-btn${activeFilter === "missing" ? " active" : ""}" data-filter="missing">Missing (${c.missing})</button>
    ${c.dnp > 0 ? `<button class="filter-btn${activeFilter === "dnp" ? " active" : ""}" data-filter="dnp">DNP (${c.dnp})</button>` : ''}
  `;
  filterBar.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      activeFilter = btn.dataset.filter;
      _deps.render();
    });
  });
  body.appendChild(filterBar);
}

// ── BOM row element builder ──

function createBomRowElement(d) {
  const tr = document.createElement("tr");
  tr.dataset.partKey = d.partKey;
  tr.className = d.rowClass;
  if (d.isLinkingSource || d.isReverseLinkingSource) tr.classList.add("linking-source");
  if (d.isReverseTarget) tr.classList.add("link-target");

  let haveHtml = "" + d.invQty;
  if (d.altBadge) {
    const coveredCls = d.altBadge.covered ? " covered" : "";
    const expandedCls = d.altBadge.expanded ? " expanded" : "";
    haveHtml += '<br><span class="alt-badge' + coveredCls + expandedCls + '" data-part-key="' + escHtml(d.partKey) + '"><span class="chevron">▸</span>+' + d.altBadge.altQty + ' (' + d.altBadge.badgeText + ')</span>';
  }

  const adjBtnHtml = d.showAdjust ? '<button class="adj-btn" title="Adjust qty">Adjust</button>' : '';
  const confirmBtnHtml = d.showConfirm
    ? '<button class="confirm-btn" title="Confirm this match">Confirm</button>'
    : d.showUnconfirm
      ? '<button class="unconfirm-btn" title="Revert to possible match">Unconfirm</button>'
      : '';
  const linkBtnHtml = d.showLink
    ? `<button class="link-btn${d.linkActive ? ' active' : ''}" title="${d.hasInv ? 'Link to missing BOM row' : 'Link to inventory part'}">Link</button>`
    : '';

  tr.innerHTML = `
    <td class="refs-cell" title="${escHtml(d.refs)}">${colorizeRefs(d.refs)}</td>
    <td class="status">${d.icon}</td>
    <td class="mono">${d.dispLcsc ? '<span' + (/^C\d{4,}$/i.test(d.dispLcsc) ? ' data-lcsc="' + escHtml(d.dispLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(d.dispLcsc) + '</span>' : ''}${d.dispLcsc && d.dispDigikey ? '<br>' : ''}${d.dispDigikey ? '<span data-digikey="' + escHtml(d.dispDigikey) + '" style="color:#cc6600"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(d.dispDigikey) + '</span>' : ''}${(d.dispLcsc || d.dispDigikey) && d.dispPololu ? '<br>' : ''}${d.dispPololu ? '<span data-pololu="' + escHtml(d.dispPololu) + '" style="color:#9b1b30"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(d.dispPololu) + '</span>' : ''}</td>
    <td class="mono" title="${escHtml(d.dispMpn)}">${escHtml(d.dispMpn)}</td>
    <td class="${d.qtyClass}" style="text-align:right;font-weight:600">${d.effectiveQty}</td>
    <td class="inv-qty-cell ${d.qtyClass}" style="text-align:right;font-weight:600">${haveHtml}</td>
    <td class="desc-cell${d.isMissing ? ' muted' : ''}" title="${escHtml(d.invDesc)}">${escHtml(d.invDesc)}</td>
    <td class="mono" style="text-align:center">${d.matchLabel}</td>
    <td class="btn-group">${confirmBtnHtml}${adjBtnHtml}${linkBtnHtml}</td>
  `;

  return tr;
}

// ── Alt rows builder ──

function renderAltRows(tbody, alts, partKey) {
  alts.forEach(alt => {
    const altTr = document.createElement("tr");
    altTr.className = "alt-row";
    altTr.dataset.altFor = partKey;
    altTr.dataset.invKey = invPartKey(alt);
    var altLcsc = alt.lcsc || '';
    var altDigikey = alt.digikey || '';
    var altPololu = alt.pololu || '';
    var altPartHtml = '';
    if (altLcsc) altPartHtml += '<span' + (/^C\d{4,}$/i.test(altLcsc) ? ' data-lcsc="' + escHtml(altLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(altLcsc) + '</span>';
    if (altLcsc && altDigikey) altPartHtml += '<br>';
    if (altDigikey) altPartHtml += '<span data-digikey="' + escHtml(altDigikey) + '" style="color:#cc6600"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(altDigikey) + '</span>';
    if ((altLcsc || altDigikey) && altPololu) altPartHtml += '<br>';
    if (altPololu) altPartHtml += '<span data-pololu="' + escHtml(altPololu) + '" style="color:#9b1b30"><img class="vendor-icon" src="data/pololu-icon.svg">' + escHtml(altPololu) + '</span>';
    altTr.innerHTML =
      '<td></td>' +
      '<td></td>' +
      '<td class="mono">' + altPartHtml + '</td>' +
      '<td class="mono" title="' + escHtml(alt.mpn || '') + '">' + escHtml(alt.mpn || '') + '</td>' +
      '<td></td>' +
      '<td style="text-align:right;font-weight:600">' + alt.qty + '</td>' +
      '<td class="desc-cell" title="' + escHtml(alt.description) + ' ' + escHtml(alt.package) + '">' + escHtml(alt.description) + ' <span class="muted">' + escHtml(alt.package) + '</span></td>' +
      '<td></td>' +
      '<td class="btn-group"><button class="swap-btn" title="Use this alt as the selected part">Swap</button><button class="adj-btn" title="Adjust qty">Adjust</button></td>';
    tbody.appendChild(altTr);
  });
}

// ── Delegated tbody click handler ──

function handleBomTableClick(e) {
  // Alt badge toggle
  const badge = e.target.closest(".alt-badge");
  if (badge) {
    e.stopPropagation();
    const pk = badge.dataset.partKey;
    if (expandedAlts.has(pk)) expandedAlts.delete(pk);
    else expandedAlts.add(pk);
    _deps.render();
    return;
  }

  // Button clicks (both main rows and alt rows)
  const btn = e.target.closest("button");
  if (btn) {
    e.stopPropagation();
    const tr = btn.closest("tr");

    // Alt row buttons
    if (tr.classList.contains("alt-row")) {
      const parentKey = tr.dataset.altFor;
      const parentRow = rowMap.get(parentKey);
      const altKey = tr.dataset.invKey;
      const alt = parentRow && parentRow.alts
        ? parentRow.alts.find(a => invPartKey(a) === altKey)
        : null;
      if (!alt) return;
      if (btn.classList.contains("adj-btn")) _deps.openAdjustModal(alt);
      else if (btn.classList.contains("swap-btn")) confirmAltMatch(parentRow, alt);
      return;
    }

    // Main row buttons
    const pk = tr.dataset.partKey;
    const r = rowMap.get(pk);
    if (!r) return;
    if (btn.classList.contains("confirm-btn")) confirmMatch(r);
    else if (btn.classList.contains("unconfirm-btn")) unconfirmMatch(r);
    else if (btn.classList.contains("adj-btn")) _deps.openAdjustModal(r.inv);
    else if (btn.classList.contains("link-btn")) {
      if (r.inv) App.links.setLinkingMode(true, r.inv);
      else if (r.effectiveStatus === "missing") App.links.setReverseLinkingMode(true, r);
    }
    return;
  }

  // Reverse linking: row click on link-target
  const tr = e.target.closest("tr.link-target");
  if (tr && !tr.classList.contains("alt-row")) {
    const pk = tr.dataset.partKey;
    const r = rowMap.get(pk);
    if (r && r.inv) _deps.createReverseLink(r.inv);
  }
}

// ── Main render ──

export function renderBomComparison(body, searchInput) {
  const query = (searchInput.value || "").toLowerCase();
  const rows = bomData.rows;
  const sortedRows = [...rows].sort((a, b) => BOM_STATUS_SORT_ORDER[a.effectiveStatus] - BOM_STATUS_SORT_ORDER[b.effectiveStatus]);
  const c = countStatuses(rows);
  const linkingState = {
    linkingMode: App.links.linkingMode,
    linkingInvItem: App.links.linkingInvItem,
    linkingBomRow: App.links.linkingBomRow,
  };

  renderFilterBar(body, c);

  // Build row lookup map for delegation
  rowMap = new Map();
  sortedRows.forEach(r => { rowMap.set(bomKey(r.bom), r); });

  // BOM matched section — table with full comparison
  const tableWrap = document.createElement("div");
  tableWrap.className = "table-wrap";
  const table = document.createElement("table");
  table.innerHTML = `<thead><tr>
    <th class="refs-col">Designators</th>
    <th style="width:24px"></th>
    <th style="width:110px">Part #</th>
    <th style="width:140px">MPN</th>
    <th style="width:50px">Need</th>
    <th style="width:50px">Have</th>
    <th>Description</th>
    <th style="width:78px;text-align:center">Match</th>
    <th class="btn-group-hdr"></th>
  </tr></thead>`;

  const tbody = document.createElement("tbody");
  sortedRows.forEach(r => {
    const d = bomRowDisplayData(r, query, activeFilter, expandedAlts, linkingState);
    if (!d) return;
    tbody.appendChild(createBomRowElement(d));
    if (d.showAlts) renderAltRows(tbody, r.alts, d.partKey);
  });

  tbody.addEventListener("click", handleBomTableClick);

  table.appendChild(tbody);
  tableWrap.appendChild(table);
  body.appendChild(tableWrap);

  // Sticky horizontal scrollbar — stays at bottom of viewport while BOM table is visible
  const stickyScroll = document.createElement("div");
  stickyScroll.className = "sticky-scrollbar";
  const stickyInner = document.createElement("div");
  stickyInner.style.height = "1px";
  stickyScroll.appendChild(stickyInner);
  body.appendChild(stickyScroll);

  function syncWidths() {
    stickyInner.style.width = table.scrollWidth + "px";
  }
  syncWidths();
  new ResizeObserver(syncWidths).observe(table);

  let syncing = false;
  stickyScroll.addEventListener("scroll", () => {
    if (syncing) return;
    syncing = true;
    tableWrap.scrollLeft = stickyScroll.scrollLeft;
    syncing = false;
  });
  tableWrap.addEventListener("scroll", () => {
    if (syncing) return;
    syncing = true;
    stickyScroll.scrollLeft = tableWrap.scrollLeft;
    syncing = false;
  });

  // Return matched inv keys so caller can render remaining inventory
  const matchedInvKeys = new Set();
  rows.forEach(r => {
    if (r.inv) {
      const pk = invPartKey(r.inv).toUpperCase();
      if (pk) matchedInvKeys.add(pk);
    }
  });
  return matchedInvKeys;
}

// ── Confirm Match Functions ──

function confirmMatch(bomRow) {
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(bomRow.inv);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  App.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed: " + bk + " → " + ipk);
  showToast("Confirmed " + bk);
}

function unconfirmMatch(bomRow) {
  const bk = bomKey(bomRow.bom);
  if (!bk) { AppLog.warn("Cannot unconfirm: missing BOM key"); return; }
  UndoRedo.save("links", snapshotLinks());
  App.links.unconfirmMatch(bk);
  AppLog.info("Unconfirmed: " + bk);
  showToast("Unconfirmed " + bk);
}

function confirmAltMatch(bomRow, altInvItem) {
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(altInvItem);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm alt: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  App.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed alt: " + bk + " → " + ipk);
  showToast("Confirmed " + bk + " → " + ipk);
}
