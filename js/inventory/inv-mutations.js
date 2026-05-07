/* inventory/inv-mutations.js — BOM match mutations and delegated click handler.
   confirmMatch, unconfirmMatch, confirmAltMatch, createReverseLink,
   inferPartType, autoCreateGroupAndOpenFlyout, handleBomTableClick. */

import { AppLog, api } from '../api.js';
import { EventBus, Events } from '../event-bus.js';
import { showToast } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, snapshotLinks } from '../store.js';
import { bomKey, invPartKey } from '../part-keys.js';
import { openAdjustModal } from '../inventory-modals.js';
import { openFlyout } from '../group-flyout/flyout-panel.js';
import state from './inv-state.js';

// ── Reverse link helper ──

export function createReverseLink(invItem) {
  var bomRow = store.links.linkingBomRow;
  if (!bomRow) return;
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(invItem);
  if (!bk || !ipk) {
    showToast("Cannot create link — missing part key");
    return;
  }
  UndoRedo.save("links", snapshotLinks());
  store.links.addManualLink(bk, ipk);
  AppLog.info("Manual link: " + ipk + " → " + bk);
  store.links.setReverseLinkingMode(false);
  showToast("Linked " + ipk + " → " + bk);
}

// ── Confirm Match Functions ──

export function confirmMatch(bomRow) {
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(bomRow.inv);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed: " + bk + " → " + ipk);
  showToast("Confirmed " + bk);
}

export function unconfirmMatch(bomRow) {
  var bk = bomKey(bomRow.bom);
  if (!bk) { AppLog.warn("Cannot unconfirm: missing BOM key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.unconfirmMatch(bk);
  AppLog.info("Unconfirmed: " + bk);
  showToast("Unconfirmed " + bk);
}

export function confirmAltMatch(bomRow, altInvItem) {
  var bk = bomKey(bomRow.bom);
  var ipk = invPartKey(altInvItem);
  if (!bk || !ipk) { AppLog.warn("Cannot confirm alt: missing part key"); return; }
  UndoRedo.save("links", snapshotLinks());
  store.links.confirmMatch(bk, ipk);
  AppLog.info("Confirmed alt: " + bk + " → " + ipk);
  showToast("Confirmed " + bk + " → " + ipk);
}

// ── Auto-create group from BOM spec ──

/**
 * Infer part type from a BOM designator string (e.g. "C1,C2" → "capacitor").
 * Falls back to "other" when unknown.
 * @param {string} refs
 * @returns {string}
 */
export function inferPartType(refs) {
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
export async function autoCreateGroupAndOpenFlyout(btn, row) {
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

export function handleBomTableClick(e) {
  // Alt badge toggle
  var badge = e.target.closest(".alt-badge");
  if (badge) {
    e.stopPropagation();
    var pk = badge.dataset.partKey;
    if (state.expandedAlts.has(pk)) state.expandedAlts.delete(pk);
    else state.expandedAlts.add(pk);
    state._render();
    return;
  }

  // Member badge toggle
  var memberBadge = e.target.closest(".member-badge");
  if (memberBadge) {
    e.stopPropagation();
    var mpk = memberBadge.dataset.partKey;
    if (state.expandedMembers.has(mpk)) state.expandedMembers.delete(mpk);
    else state.expandedMembers.add(mpk);
    state._render();
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
          AppLog.info("Generic member selected: " + memberParentKey + " → " + memberPartId);
          showToast("Confirmed " + memberParentKey + " → " + memberPartId);
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
