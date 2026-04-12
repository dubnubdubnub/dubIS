// @ts-check
/* flyout-drag.js — Drag-out member removal and drag-in from main inventory */

import { flyouts } from './flyout-state.js';
import { api, AppLog } from '../api.js';

// Imported lazily to avoid circular dependency (flyout-panel imports us).
/** @type {Function | null} */
var _rerenderFlyout = null;

/**
 * Called by flyout-panel.js to supply the rerenderFlyout function.
 * @param {Function} fn
 */
export function setRerenderFlyout(fn) {
  _rerenderFlyout = fn;
}

// ── Stored listener references for removal ────────────────────────────────────

/** @type {EventListener | null} */
var _onInvDragstart = null;

// ── Helper: get gpId from a descendant element ────────────────────────────────

/**
 * @param {Element} el
 * @returns {string | null}
 */
function getFlyoutGpId(el) {
  var flyoutEl = /** @type {HTMLElement | null} */ (el.closest(".group-flyout"));
  return flyoutEl ? (flyoutEl.dataset.gpId || null) : null;
}

// ── Remove member (drag-out flow) ────────────────────────────────────────────

/**
 * Remove a member from a group: call remove + exclude API, update state, rerender.
 * @param {string} gpId
 * @param {string} partId
 */
async function removeMember(gpId, partId) {
  var inst = flyouts.get(gpId);
  if (!inst) return;

  try {
    await api("remove_generic_member", gpId, partId);
    await api("exclude_generic_member", gpId, partId);
  } catch (e) {
    AppLog.error("flyout-drag: remove/exclude member failed: " + e);
    return;
  }

  // Remove from allMembers
  inst.allMembers = inst.allMembers.filter(function (m) {
    return m.part_id !== partId;
  });

  if (_rerenderFlyout) _rerenderFlyout(gpId);
}

// ── Add member (drag-in flow) ────────────────────────────────────────────────

/**
 * Add an inventory part to a group: call add API, update state, rerender.
 * @param {string} gpId
 * @param {string} partId
 */
async function addMember(gpId, partId) {
  var inst = flyouts.get(gpId);
  if (!inst) return;

  // Don't add if already a member
  for (var i = 0; i < inst.allMembers.length; i++) {
    if (inst.allMembers[i].part_id === partId) return;
  }

  var updatedMembers;
  try {
    updatedMembers = await api("add_generic_member", gpId, partId);
  } catch (e) {
    AppLog.error("flyout-drag: add_generic_member failed: " + e);
    return;
  }

  // Use the returned members list to update state
  if (Array.isArray(updatedMembers)) {
    inst.allMembers = updatedMembers;
  }

  // Regenerate tags to include any new spec dimensions from the new member
  // We need the gp spec from the inst — use the tags already in inst and just rerender
  if (_rerenderFlyout) _rerenderFlyout(gpId);
}

// ── wireDrag ──────────────────────────────────────────────────────────────────

/**
 * Wire drag events on the flyout container element.
 * Handles:
 *   - dragstart/dragend on .flyout-member (drag-out to remove)
 *   - dragover/dragleave/drop on container (drag-in from inventory)
 * @param {HTMLElement} container
 */
export function wireDrag(container) {
  // Drag-out: member being dragged out of the flyout
  container.addEventListener("dragstart", function (e) {
    var target = /** @type {HTMLElement} */ (e.target);
    var memberEl = /** @type {HTMLElement | null} */ (target.closest(".flyout-member"));
    if (!memberEl) return;

    var partId = memberEl.dataset.partId || "";
    if (!partId) return;

    memberEl.classList.add("dragging");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("application/x-flyout-member", partId);
      var gpId = getFlyoutGpId(memberEl) || "";
      e.dataTransfer.setData("application/x-flyout-gp-id", gpId);
    }
  });

  container.addEventListener("dragend", function (e) {
    var target = /** @type {HTMLElement} */ (e.target);
    var memberEl = /** @type {HTMLElement | null} */ (target.closest(".flyout-member"));
    if (!memberEl) return;

    memberEl.classList.remove("dragging");

    // If dropped outside (no valid drop target), remove the member
    if (e.dataTransfer && e.dataTransfer.dropEffect === "none") {
      var partId = memberEl.dataset.partId || "";
      var gpId = getFlyoutGpId(memberEl) || "";
      if (partId && gpId) {
        removeMember(gpId, partId);
      }
    }
  });

  // Drag-in: inventory part being dragged into a flyout
  container.addEventListener("dragover", function (e) {
    if (!e.dataTransfer) return;
    var types = e.dataTransfer.types;
    var hasInvPart = false;
    for (var i = 0; i < types.length; i++) {
      if (types[i] === "application/x-inv-part") { hasInvPart = true; break; }
    }
    if (!hasInvPart) return;

    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";

    var flyoutEl = /** @type {HTMLElement | null} */ (
      /** @type {HTMLElement} */ (e.target).closest(".group-flyout")
    );
    if (flyoutEl) flyoutEl.classList.add("drop-hover");
  });

  container.addEventListener("dragleave", function (e) {
    var flyoutEl = /** @type {HTMLElement | null} */ (
      /** @type {HTMLElement} */ (e.target).closest(".group-flyout")
    );
    if (!flyoutEl) return;

    // Only remove drop-hover if leaving the flyout element itself (not entering a child)
    var related = /** @type {Node | null} */ (e.relatedTarget);
    if (!related || !flyoutEl.contains(related)) {
      flyoutEl.classList.remove("drop-hover");
    }
  });

  container.addEventListener("drop", function (e) {
    var flyoutEl = /** @type {HTMLElement | null} */ (
      /** @type {HTMLElement} */ (e.target).closest(".group-flyout")
    );
    if (flyoutEl) flyoutEl.classList.remove("drop-hover");

    if (!e.dataTransfer) return;
    var partId = e.dataTransfer.getData("application/x-inv-part");
    if (!partId) return;

    e.preventDefault();

    var gpId = flyoutEl ? (flyoutEl.dataset.gpId || "") : "";
    if (!gpId) {
      AppLog.warn("flyout-drag: drop on container with no gpId");
      return;
    }

    addMember(gpId, partId);
  });
}

// ── wireInventoryDrag ─────────────────────────────────────────────────────────

/**
 * Wire dragstart on #inventory-body for inventory rows.
 * Called when the first flyout opens.
 */
export function wireInventoryDrag() {
  if (_onInvDragstart) return; // already wired

  var invBody = document.getElementById("inventory-body");
  if (!invBody) {
    AppLog.warn("flyout-drag: #inventory-body not found");
    return;
  }

  _onInvDragstart = function (e) {
    var target = /** @type {HTMLElement} */ (e.target);
    // Support both .inv-part-row divs (normal mode) and <tr> rows (BOM mode)
    var rowEl = /** @type {HTMLElement | null} */ (
      target.closest(".inv-part-row, tr[data-part-key]")
    );
    if (!rowEl) return;

    var partId = rowEl.dataset.partId || rowEl.dataset.partKey || "";
    if (!partId) return;

    var dragEvent = /** @type {DragEvent} */ (e);
    if (dragEvent.dataTransfer) {
      dragEvent.dataTransfer.effectAllowed = "copy";
      dragEvent.dataTransfer.setData("application/x-inv-part", partId);
    }
  };

  invBody.addEventListener("dragstart", _onInvDragstart);
}

// ── unwireInventoryDrag ───────────────────────────────────────────────────────

/**
 * Remove the dragstart listener from #inventory-body.
 * Called when all flyouts close.
 */
export function unwireInventoryDrag() {
  if (!_onInvDragstart) return;

  var invBody = document.getElementById("inventory-body");
  if (invBody) {
    invBody.removeEventListener("dragstart", _onInvDragstart);
  }

  _onInvDragstart = null;
}
