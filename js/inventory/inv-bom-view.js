/* inv-bom-view.js — BOM comparison view for the inventory panel.
   Extracted from inventory-panel.js. Pure rendering of the BOM comparison
   section: filter bar, matched table, confirm/unconfirm match logic. */

import { AppLog } from '../api.js';
import { showToast } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, snapshotLinks } from '../store.js';
import { bomKey, invPartKey, countStatuses } from '../part-keys.js';
import { openAdjustModal } from '../inventory-modals.js';
import { openCreate as openGenericCreate } from '../generic-parts-modal.js';
import {
  sortBomRows,
  bomRowDisplayData,
  computeMatchedInvKeys,
  buildRowMap,
} from './inventory-logic.js';
import {
  createBomRowElement,
  renderAltRows,
  renderMemberRows,
  renderFilterBarHtml,
  renderBomTableHeader,
} from './inventory-renderer.js';
import state from './inv-state.js';

// ── BOM Comparison ──

/**
 * Render the BOM comparison section into state.body.
 * @param {function(): void} render - full panel re-render callback
 * @param {function(Object): void} createReverseLink - reverse-link callback
 * @returns {Set<string>} matched inventory part keys (uppercased)
 */
export function renderBomComparison(render, createReverseLink) {
  const query = (state.searchInput.value || "").toLowerCase();
  const rows = state.bomData.rows;
  const sortedRows = sortBomRows(rows);
  const c = countStatuses(rows);
  const linkingState = {
    linkingMode: store.links.linkingMode,
    linkingInvItem: store.links.linkingInvItem,
    linkingBomRow: store.links.linkingBomRow,
  };

  // Filter bar
  const filterBar = document.createElement("div");
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
  const tableWrap = document.createElement("div");
  tableWrap.className = "table-wrap";
  const table = document.createElement("table");
  table.innerHTML = renderBomTableHeader();

  const tbody = document.createElement("tbody");
  for (let i = 0; i < sortedRows.length; i++) {
    const r = sortedRows[i];
    const d = bomRowDisplayData(r, query, state.activeFilter, state.expandedAlts, linkingState, state.expandedMembers);
    if (!d) continue;
    tbody.appendChild(createBomRowElement(d));
    if (d.showAlts) {
      const altElements = renderAltRows(r.alts, d.partKey);
      for (let j = 0; j < altElements.length; j++) {
        tbody.appendChild(altElements[j]);
      }
    }
    if (d.showMembers && d.genericMembers) {
      const resolvedId = r.inv ? invPartKey(r.inv) : "";
      const memberElements = renderMemberRows(d.genericMembers, d.partKey, resolvedId, d.genericPartName || "", store.inventory);
      for (let m = 0; m < memberElements.length; m++) {
        tbody.appendChild(memberElements[m]);
      }
    }
  }

  tbody.addEventListener("click", function (e) { handleBomTableClick(e, render, createReverseLink); });

  table.appendChild(tbody);
  tableWrap.appendChild(table);
  state.body.appendChild(tableWrap);

  // Sticky horizontal scrollbar
  const stickyScroll = document.createElement("div");
  stickyScroll.className = "sticky-scrollbar";
  const stickyInner = document.createElement("div");
  stickyInner.style.height = "1px";
  stickyScroll.appendChild(stickyInner);
  state.body.appendChild(stickyScroll);

  function syncWidths() {
    stickyInner.style.width = table.scrollWidth + "px";
  }
  syncWidths();
  new ResizeObserver(syncWidths).observe(table);

  let syncing = false;
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

function handleBomTableClick(e, render, createReverseLink) {
  // Alt badge toggle
  const badge = e.target.closest(".alt-badge");
  if (badge) {
    e.stopPropagation();
    const pk = badge.dataset.partKey;
    if (state.expandedAlts.has(pk)) state.expandedAlts.delete(pk);
    else state.expandedAlts.add(pk);
    render();
    return;
  }

  // Member badge toggle
  const memberBadge = e.target.closest(".member-badge");
  if (memberBadge) {
    e.stopPropagation();
    const mpk = memberBadge.dataset.partKey;
    if (state.expandedMembers.has(mpk)) state.expandedMembers.delete(mpk);
    else state.expandedMembers.add(mpk);
    render();
    return;
  }

  // Button clicks (both main rows and alt rows)
  const btn = e.target.closest("button");
  if (btn) {
    e.stopPropagation();
    const tr = btn.closest("tr");

    // Member row buttons
    if (tr.classList.contains("member-row")) {
      const memberPartId = tr.dataset.memberPartId;
      const memberParentKey = tr.dataset.memberFor;
      if (btn.classList.contains("use-member-btn")) {
        // Confirm match with this specific member
        const memberR = state.rowMap.get(memberParentKey);
        if (memberR) {
          UndoRedo.save("links", snapshotLinks());
          store.links.confirmMatch(bomKey(memberR.bom), memberPartId);
          AppLog.info("Generic member selected: " + memberParentKey + " \u2192 " + memberPartId);
          showToast("Confirmed " + memberParentKey + " \u2192 " + memberPartId);
          state.expandedMembers.delete(memberParentKey);
        }
      } else if (btn.classList.contains("adj-btn")) {
        // Find the inventory item for this member
        for (let mi = 0; mi < store.inventory.length; mi++) {
          const mItem = store.inventory[mi];
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
      const parentKey = tr.dataset.altFor;
      const parentRow = state.rowMap.get(parentKey);
      const altKey = tr.dataset.invKey;
      const alt = parentRow && parentRow.alts
        ? parentRow.alts.find(function (a) { return invPartKey(a) === altKey; })
        : null;
      if (!alt) return;
      if (btn.classList.contains("adj-btn")) openAdjustModal(alt);
      else if (btn.classList.contains("swap-btn")) confirmAltMatch(parentRow, alt);
      return;
    }

    // Main row buttons
    const rowPk = tr.dataset.partKey;
    const r = state.rowMap.get(rowPk);
    if (!r) return;
    if (btn.classList.contains("create-generic-btn")) {
      const typeMap = { C: "capacitor", R: "resistor", L: "inductor" };
      const refChar = (btn.dataset.bomRefs || "").trim().charAt(0).toUpperCase();
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
      if (r.inv) store.links.setLinkingMode(true, r.inv);
      else if (r.effectiveStatus === "missing") store.links.setReverseLinkingMode(true, r);
    }
    return;
  }

  // Reverse linking: row click on link-target
  const linkTr = e.target.closest("tr.link-target");
  if (linkTr && !linkTr.classList.contains("alt-row")) {
    const linkPk = linkTr.dataset.partKey;
    const linkR = state.rowMap.get(linkPk);
    if (linkR && linkR.inv) createReverseLink(linkR.inv);
  }
}

// ── Confirm Match Functions ──

function confirmMatch(bomRow) {
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(bomRow.inv);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed: " + bk + " \u2192 " + ipk);
  showToast("Confirmed " + bk);
}

function unconfirmMatch(bomRow) {
  const bk = bomKey(bomRow.bom);
  if (!bk) { AppLog.warn("Cannot unconfirm: missing BOM key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.unconfirmMatch(bk);
  AppLog.info("Unconfirmed: " + bk);
  showToast("Unconfirmed " + bk);
}

function confirmAltMatch(bomRow, altInvItem) {
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(altInvItem);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm alt: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed alt: " + bk + " \u2192 " + ipk);
  showToast("Confirmed " + bk + " \u2192 " + ipk);
}
