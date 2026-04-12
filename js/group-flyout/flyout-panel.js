// @ts-check
/* flyout-panel.js — Public API for opening, closing, positioning, and rendering flyouts */

import { EventBus, Events } from '../event-bus.js';
import { api, AppLog } from '../api.js';
import { App } from '../store.js';
import { generateTags, filterMembers } from './flyout-logic.js';
import { renderFlyout } from './flyout-renderer.js';
import { flyouts, activeFlyoutId, setActiveFlyoutId } from './flyout-state.js';
import { wireEvents, setPanelFunctions } from './flyout-events.js';
import { wireDrag, wireInventoryDrag, unwireInventoryDrag, setRerenderFlyout } from './flyout-drag.js';

/** @type {HTMLElement | null} */
var _container = null;

// ── renderFlyoutInstance ──────────────────────────────────────────────────────

/**
 * Apply filterMembers, compute totalStock, and call renderFlyout.
 * @param {import('./flyout-state.js').FlyoutInstance} inst
 * @param {boolean} isActive
 * @returns {string}
 */
function renderFlyoutInstance(inst, isActive) {
  var filtered = filterMembers(inst.allMembers, inst.tags, inst.searchText);
  var totalStock = 0;
  for (var i = 0; i < filtered.length; i++) {
    totalStock += (/** @type {any} */ (filtered[i]).qty || 0);
  }
  return renderFlyout({
    genericPartId: inst.genericPartId,
    groupName: inst.groupName,
    tags: inst.tags,
    filteredMembers: filtered,
    totalStock: totalStock,
    searchText: inst.searchText,
    isActive: isActive,
    frozen: inst.frozen,
    savedSearches: inst.savedSearches,
    activeSavedSearchId: inst.savedSearchId,
  });
}

// ── positionFlyout ────────────────────────────────────────────────────────────

/**
 * Vertically center the flyout on sourceRowEl, clamped within container bounds.
 * @param {import('./flyout-state.js').FlyoutInstance} inst
 */
function positionFlyout(inst) {
  if (!inst.el || !_container) return;

  var containerRect = _container.getBoundingClientRect();

  var flyoutH = inst.el.offsetHeight;
  var flyoutW = inst.el.offsetWidth;
  var rowTop = 0;
  var rowH = 0;
  var rowRight = containerRect.width; // default: right edge of container

  if (inst.sourceRowEl) {
    var rowRect = inst.sourceRowEl.getBoundingClientRect();
    rowTop = rowRect.top - containerRect.top;
    rowH = rowRect.height;
    rowRight = rowRect.right - containerRect.left;
  }

  // Vertically center on the row
  var idealTop = rowTop + (rowH / 2) - (flyoutH / 2);

  // Clamp vertically
  var minTop = 0;
  var maxTop = Math.max(0, _container.offsetHeight - flyoutH);
  if (idealTop < minTop) idealTop = minTop;
  if (idealTop > maxTop) idealTop = maxTop;

  // Horizontally: place flyout to the right of the source row, clamped to container
  var idealLeft = rowRight + 8; // 8px gap after row
  var maxLeft = Math.max(0, _container.offsetWidth - flyoutW);
  if (idealLeft > maxLeft) idealLeft = maxLeft;
  if (idealLeft < 0) idealLeft = 0;

  inst.el.style.top = Math.round(idealTop) + "px";
  inst.el.style.left = Math.round(idealLeft) + "px";
}

// ── rearrangeFlyouts ──────────────────────────────────────────────────────────

var FLYOUT_GAP = 8;

/**
 * Sort flyouts by their current top position and push overlapping ones down.
 */
function rearrangeFlyouts() {
  if (!_container) return;

  // Collect all instances with DOM elements
  var insts = [];
  flyouts.forEach(function (inst) {
    if (inst.el) insts.push(inst);
  });

  // Sort by current top
  insts.sort(function (a, b) {
    var aTop = parseInt(a.el.style.top, 10) || 0;
    var bTop = parseInt(b.el.style.top, 10) || 0;
    return aTop - bTop;
  });

  // Push overlapping flyouts down
  var containerH = _container.offsetHeight;
  for (var i = 1; i < insts.length; i++) {
    var prev = insts[i - 1];
    var curr = insts[i];
    var prevTop = parseInt(prev.el.style.top, 10) || 0;
    var prevBottom = prevTop + prev.el.offsetHeight + FLYOUT_GAP;
    var currTop = parseInt(curr.el.style.top, 10) || 0;
    if (currTop < prevBottom) {
      currTop = prevBottom;
      // Clamp to container
      var maxTop = Math.max(0, containerH - curr.el.offsetHeight);
      if (currTop > maxTop) currTop = maxTop;
      curr.el.style.top = Math.round(currTop) + "px";
    }
  }
}

// ── activateFlyout ────────────────────────────────────────────────────────────

/**
 * Set a flyout as the active one: update class + state + emit event.
 * @param {string} genericPartId
 */
export function activateFlyout(genericPartId) {
  setActiveFlyoutId(genericPartId);

  flyouts.forEach(function (inst, id) {
    if (!inst.el) return;
    if (id === genericPartId) {
      inst.el.classList.add("flyout-active");
    } else {
      inst.el.classList.remove("flyout-active");
    }
  });

  EventBus.emit(Events.FLYOUT_ACTIVE_CHANGED, { gpId: genericPartId });
}

// ── closeFlyout ───────────────────────────────────────────────────────────────

/**
 * Remove a flyout's DOM element, delete from map, update active id, rearrange.
 * @param {string} genericPartId
 */
export function closeFlyout(genericPartId) {
  var inst = flyouts.get(genericPartId);
  if (!inst) return;

  if (inst.el && inst.el.parentNode) {
    inst.el.parentNode.removeChild(inst.el);
  }

  flyouts.delete(genericPartId);

  // If the closed flyout was active, activate another one (or null)
  if (activeFlyoutId === genericPartId) {
    var nextId = null;
    flyouts.forEach(function (_, id) { if (!nextId) nextId = id; });
    if (nextId) {
      activateFlyout(nextId);
    } else {
      setActiveFlyoutId(null);
    }
  }

  rearrangeFlyouts();

  // Unwire inventory drag when last flyout closes
  if (flyouts.size === 0) {
    unwireInventoryDrag();
  }

  EventBus.emit(Events.FLYOUT_CLOSED, { gpId: genericPartId });
}

// ── rerenderFlyout ────────────────────────────────────────────────────────────

/**
 * Re-render a flyout's contents in place, replacing its outerHTML.
 * If frozen and has frozenMemberIds, filter allMembers to those IDs first.
 * @param {string} genericPartId
 */
export function rerenderFlyout(genericPartId) {
  var inst = flyouts.get(genericPartId);
  if (!inst || !inst.el) return;

  // If frozen, restrict allMembers to frozenMemberIds before rendering
  var savedAllMembers = null;
  if (inst.frozen && inst.frozenMemberIds) {
    savedAllMembers = inst.allMembers;
    var ids = inst.frozenMemberIds;
    inst.allMembers = inst.allMembers.filter(function (m) {
      return ids.indexOf(m.part_id) !== -1;
    });
  }

  var isActive = (activeFlyoutId === genericPartId);
  var html = renderFlyoutInstance(inst, isActive);

  // Replace outerHTML and re-acquire DOM reference
  var placeholder = document.createElement("div");
  inst.el.insertAdjacentElement("afterend", placeholder);
  inst.el.remove();
  placeholder.outerHTML = html;

  // Re-acquire DOM ref
  var newEl = _container ? _container.querySelector('.group-flyout[data-gp-id="' + genericPartId + '"]') : null;
  inst.el = /** @type {HTMLElement | null} */ (newEl);

  // Restore allMembers if we swapped it
  if (savedAllMembers !== null) {
    inst.allMembers = savedAllMembers;
  }
}

// ── openFlyout ────────────────────────────────────────────────────────────────

/**
 * Open (or activate if already open) a flyout for a generic part.
 * @param {string} genericPartId
 * @param {HTMLElement | null} sourceRowEl
 */
export async function openFlyout(genericPartId, sourceRowEl) {
  // If already open, just activate it
  if (flyouts.has(genericPartId)) {
    activateFlyout(genericPartId);
    return;
  }

  if (!_container) {
    AppLog.error("flyout: container not initialized");
    return;
  }

  // Find the generic part in App.genericParts
  var gp = null;
  var gps = App.genericParts;
  for (var i = 0; i < gps.length; i++) {
    if (String(gps[i].generic_part_id) === String(genericPartId)) {
      gp = gps[i];
      break;
    }
  }

  if (!gp) {
    AppLog.warn("flyout: generic part not found: " + genericPartId);
    return;
  }

  var allMembers = Array.isArray(gp.members) ? gp.members : [];
  var tags = generateTags(gp.spec || {}, gp.strictness || {}, allMembers);

  // Load saved searches
  var savedSearches = [];
  try {
    var result = await api("list_saved_searches", genericPartId);
    if (Array.isArray(result)) savedSearches = result;
  } catch (e) {
    AppLog.warn("flyout: list_saved_searches failed: " + e);
  }

  /** @type {import('./flyout-state.js').FlyoutInstance} */
  var inst = {
    genericPartId: String(genericPartId),
    groupName: gp.name || ("Group " + genericPartId),
    tags: tags,
    searchText: "",
    allMembers: allMembers,
    el: null,
    sourceRowEl: sourceRowEl || null,
    frozen: false,
    frozenMemberIds: null,
    savedSearchId: null,
    savedSearches: savedSearches,
  };

  flyouts.set(String(genericPartId), inst);

  // Wire inventory drag when first flyout opens
  if (flyouts.size === 1) {
    wireInventoryDrag();
  }

  // Render HTML and insert into container
  var isActive = true;
  var html = renderFlyoutInstance(inst, isActive);

  var temp = document.createElement("div");
  temp.innerHTML = html;
  var flyoutEl = /** @type {HTMLElement} */ (temp.firstElementChild);
  _container.appendChild(flyoutEl);
  inst.el = flyoutEl;

  // Position it
  positionFlyout(inst);
  rearrangeFlyouts();

  // Activate
  activateFlyout(String(genericPartId));

  EventBus.emit(Events.FLYOUT_OPENED, { gpId: String(genericPartId) });
}

// ── init ──────────────────────────────────────────────────────────────────────

/**
 * Find the #flyout-container element and wire events.
 */
export function init() {
  var el = document.getElementById("flyout-container");
  if (!el) {
    AppLog.error("flyout: #flyout-container not found");
    return;
  }
  _container = /** @type {HTMLElement} */ (el);

  // Provide panel functions to events module (avoids circular import)
  setPanelFunctions({
    openFlyout: openFlyout,
    closeFlyout: closeFlyout,
    activateFlyout: activateFlyout,
    rerenderFlyout: rerenderFlyout,
  });

  // Provide rerenderFlyout to drag module (avoids circular import)
  setRerenderFlyout(rerenderFlyout);

  wireEvents(_container);
  wireDrag(_container);
  AppLog.info("flyout: initialized");
}
