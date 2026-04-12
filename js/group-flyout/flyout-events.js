// @ts-check
/* flyout-events.js — Event delegation and interaction wiring for flyout panels */

import { EventBus, Events } from '../event-bus.js';
import { generateDefaultSearchName } from './flyout-logic.js';
import { flyouts, activeFlyoutId } from './flyout-state.js';
import { api, AppLog } from '../api.js';

// Imported lazily to avoid circular dependency (flyout-panel imports us too).
// We call these by name at runtime after module graph is fully resolved.
/** @type {Function} */
var _closeFlyout;
/** @type {Function} */
var _activateFlyout;
/** @type {Function} */
var _rerenderFlyout;

/**
 * Called by flyout-panel.js after it creates its functions, so we can call back.
 * @param {{ openFlyout: Function, closeFlyout: Function, activateFlyout: Function, rerenderFlyout: Function }} fns
 */
export function setPanelFunctions(fns) {
  _closeFlyout = fns.closeFlyout;
  _activateFlyout = fns.activateFlyout;
  _rerenderFlyout = fns.rerenderFlyout;
}

// ── Drag state ────────────────────────────────────────────────────────────────

var _dragInst = null;   // FlyoutInstance being dragged
var _dragStartY = 0;
var _dragStartTop = 0;

// ── Container reference ───────────────────────────────────────────────────────

/** @type {HTMLElement | null} */
var _container = null;

// ── Stored listener references for removal ────────────────────────────────────

var _onContainerClick = null;
var _onContainerInput = null;
var _onContainerKeydown = null;
var _onContainerMousedown = null;
var _onDocMousemove = null;
var _onDocMouseup = null;

// ── Highlight helpers ─────────────────────────────────────────────────────────

/**
 * Walk text nodes under `el`, wrap matches of `text` in <span class="tag-highlight">.
 * @param {HTMLElement} el
 * @param {string} text - the label to highlight (case-insensitive)
 */
function applyHighlight(el, text) {
  if (!el || !text) return;
  var lower = text.toLowerCase();
  var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  var nodes = [];
  var node;
  while ((node = walker.nextNode())) nodes.push(node);

  for (var i = 0; i < nodes.length; i++) {
    var textNode = nodes[i];
    var content = textNode.nodeValue || "";
    var idx = content.toLowerCase().indexOf(lower);
    if (idx === -1) continue;

    var before = document.createTextNode(content.substring(0, idx));
    var match = document.createElement("span");
    match.className = "tag-highlight";
    match.textContent = content.substring(idx, idx + text.length);
    var after = document.createTextNode(content.substring(idx + text.length));

    var parent = textNode.parentNode;
    parent.insertBefore(before, textNode);
    parent.insertBefore(match, textNode);
    parent.insertBefore(after, textNode);
    parent.removeChild(textNode);
  }
}

/**
 * Remove all <span class="tag-highlight"> wrappers under el, unwrapping their text.
 * @param {HTMLElement} el
 */
function clearHighlights(el) {
  if (!el) return;
  var spans = el.querySelectorAll("span.tag-highlight");
  for (var i = 0; i < spans.length; i++) {
    var span = spans[i];
    var text = document.createTextNode(span.textContent || "");
    span.parentNode.replaceChild(text, span);
  }
  // Normalize to merge adjacent text nodes
  el.normalize();
}

// ── Tag hover highlighting ────────────────────────────────────────────────────

/**
 * @param {HTMLElement} flyoutEl
 * @param {string} label
 */
function highlightTagInFlyout(flyoutEl, label) {
  var descs = flyoutEl.querySelectorAll(".flyout-member-desc, .flyout-member-id");
  for (var i = 0; i < descs.length; i++) {
    applyHighlight(/** @type {HTMLElement} */ (descs[i]), label);
  }
}

/**
 * @param {HTMLElement} flyoutEl
 */
function clearTagHighlightsInFlyout(flyoutEl) {
  var descs = flyoutEl.querySelectorAll(".flyout-member-desc, .flyout-member-id");
  for (var i = 0; i < descs.length; i++) {
    clearHighlights(/** @type {HTMLElement} */ (descs[i]));
  }
}

// ── Helper: get gpId from a descendant element ────────────────────────────────

/**
 * @param {Element} el
 * @returns {string | null}
 */
function getFlyoutId(el) {
  var flyoutEl = /** @type {HTMLElement | null} */ (el.closest(".group-flyout"));
  return (flyoutEl && flyoutEl.dataset.gpId) ? flyoutEl.dataset.gpId : null;
}

// ── thawFlyout ────────────────────────────────────────────────────────────────

/**
 * @param {{ frozen: boolean, frozenMemberIds: string[] | null, savedSearchId: string | null }} inst
 */
function thawFlyout(inst) {
  inst.frozen = false;
  inst.frozenMemberIds = null;
  inst.savedSearchId = null;
}

// ── promoteSearch: convert search text to a tag (or enable existing) ──────────

/**
 * @param {{ tags: Array<{label:string,dimension:string,enabled:boolean,source:string}>, searchText: string }} inst
 */
function promoteSearch(inst) {
  var text = (inst.searchText || "").trim();
  if (!text) return;

  // Try to enable an existing tag whose label matches (case-insensitive)
  var lower = text.toLowerCase();
  var found = false;
  for (var i = 0; i < inst.tags.length; i++) {
    if (inst.tags[i].label.toLowerCase() === lower) {
      inst.tags[i].enabled = true;
      found = true;
      break;
    }
  }

  if (!found) {
    // Add a new user-defined tag with dimension "search"
    inst.tags.push({ label: text, dimension: "search", enabled: true, source: "user" });
  }

  inst.searchText = "";
}

// ── Click handler ─────────────────────────────────────────────────────────────

/**
 * @param {MouseEvent} e
 */
function handleClick(e) {
  var target = /** @type {HTMLElement} */ (e.target);

  // Determine which flyout was clicked
  var gpId = getFlyoutId(target);
  if (!gpId) return;

  var inst = flyouts.get(gpId);
  if (!inst) return;

  // Activate on any click if not already active
  if (gpId !== activeFlyoutId) {
    _activateFlyout(gpId);
  }

  // Close button
  if (target.closest(".flyout-close-btn")) {
    _closeFlyout(gpId);
    return;
  }

  // Tag button
  var tagBtn = /** @type {HTMLElement | null} */ (target.closest(".flyout-tag"));
  if (tagBtn) {
    var dim = tagBtn.dataset.dim;
    var label = tagBtn.dataset.label;
    // Toggle enabled on matching tag
    for (var i = 0; i < inst.tags.length; i++) {
      if (inst.tags[i].dimension === dim && inst.tags[i].label === label) {
        inst.tags[i].enabled = !inst.tags[i].enabled;
        break;
      }
    }
    thawFlyout(inst);
    _rerenderFlyout(gpId);
    return;
  }

  // Promote button
  if (target.closest(".flyout-promote-btn")) {
    promoteSearch(inst);
    thawFlyout(inst);
    _rerenderFlyout(gpId);
    return;
  }

  // Save button
  if (target.closest(".flyout-save-btn")) {
    var defaultName = generateDefaultSearchName(inst.tags);
    var name = window.prompt("Save search as:", defaultName || "My search");
    if (!name) return;
    name = name.trim();
    if (!name) return;

    api("create_saved_search", inst.genericPartId, name,
      inst.tags.map(function (t) { return { label: t.label, dimension: t.dimension, enabled: t.enabled }; }),
      inst.searchText || ""
    ).then(function (result) {
      if (!result || !result.id) {
        AppLog.warn("flyout: create_saved_search returned no id");
        return;
      }
      inst.savedSearches.push({ id: result.id, name: name });
      _rerenderFlyout(gpId);
    });
    return;
  }

  // Saved-search tab
  var savedTab = /** @type {HTMLElement | null} */ (target.closest(".flyout-saved-tab"));
  if (savedTab) {
    var searchId = savedTab.dataset.searchId || "";
    if (!searchId) {
      // "Live" tab — unfreeze
      thawFlyout(inst);
      inst.savedSearchId = null;
      _rerenderFlyout(gpId);
      return;
    }

    // Load from API
    api("get_saved_search", inst.genericPartId, searchId).then(function (result) {
      if (!result) {
        AppLog.warn("flyout: get_saved_search returned nothing for id=" + searchId);
        return;
      }
      // Restore tag state
      if (Array.isArray(result.tags)) {
        // Merge: update enabled state on existing tags, add new ones
        var incomingMap = {};
        for (var j = 0; j < result.tags.length; j++) {
          var rt = result.tags[j];
          incomingMap[rt.dimension + ":" + rt.label] = rt;
        }
        for (var k = 0; k < inst.tags.length; k++) {
          var key = inst.tags[k].dimension + ":" + inst.tags[k].label;
          if (incomingMap[key] !== undefined) {
            inst.tags[k].enabled = incomingMap[key].enabled;
          } else {
            inst.tags[k].enabled = false;
          }
        }
      }

      if (result.searchText !== undefined) inst.searchText = result.searchText || "";

      // Freeze: show only members that matched at the time of saving
      if (Array.isArray(result.frozenMemberIds) && result.frozenMemberIds.length > 0) {
        inst.frozen = true;
        inst.frozenMemberIds = result.frozenMemberIds;
      } else {
        inst.frozen = false;
        inst.frozenMemberIds = null;
      }
      inst.savedSearchId = searchId;
      _rerenderFlyout(gpId);
    });
    return;
  }
}

// ── Input handler ─────────────────────────────────────────────────────────────

/**
 * @param {Event} e
 */
function handleInput(e) {
  var target = /** @type {HTMLElement} */ (e.target);
  if (!target.classList.contains("flyout-search-input")) return;

  var gpId = getFlyoutId(target);
  if (!gpId) return;
  var inst = flyouts.get(gpId);
  if (!inst) return;

  inst.searchText = /** @type {HTMLInputElement} */ (target).value;
  thawFlyout(inst);
  _rerenderFlyout(gpId);

  if (gpId === activeFlyoutId) {
    EventBus.emit(Events.FLYOUT_SEARCH_CHANGED, { gpId: gpId, searchText: inst.searchText });
  }
}

// ── Keydown handler ───────────────────────────────────────────────────────────

/**
 * @param {KeyboardEvent} e
 */
function handleKeydown(e) {
  var target = /** @type {HTMLElement} */ (e.target);
  if (!target.classList.contains("flyout-search-input")) return;
  if (e.key !== "Enter") return;

  var gpId = getFlyoutId(target);
  if (!gpId) return;
  var inst = flyouts.get(gpId);
  if (!inst) return;

  promoteSearch(inst);
  thawFlyout(inst);
  _rerenderFlyout(gpId);
}

// ── Mousedown — drag handle ───────────────────────────────────────────────────

/**
 * @param {MouseEvent} e
 */
function handleMousedown(e) {
  var target = /** @type {HTMLElement} */ (e.target);
  if (!target.closest(".flyout-drag-handle")) return;

  var gpId = getFlyoutId(target);
  if (!gpId) return;
  var inst = flyouts.get(gpId);
  if (!inst || !inst.el) return;

  _dragInst = inst;
  _dragStartY = e.clientY;
  _dragStartTop = parseInt(inst.el.style.top, 10) || 0;
  e.preventDefault();
}

// ── Mousemove — drag ─────────────────────────────────────────────────────────

/**
 * @param {MouseEvent} e
 */
function handleMousemove(e) {
  if (!_dragInst || !_dragInst.el) return;

  var dy = e.clientY - _dragStartY;
  var newTop = _dragStartTop + dy;

  // Clamp within container
  if (_container) {
    var containerH = _container.offsetHeight;
    var flyoutH = _dragInst.el.offsetHeight;
    var minTop = 0;
    var maxTop = Math.max(0, containerH - flyoutH);
    if (newTop < minTop) newTop = minTop;
    if (newTop > maxTop) newTop = maxTop;
  }

  _dragInst.el.style.top = newTop + "px";
}

// ── Mouseup — end drag ────────────────────────────────────────────────────────

function handleMouseup() {
  _dragInst = null;
}

// ── Tag hover: highlight matching text in member rows ─────────────────────────

/**
 * @param {MouseEvent} e
 */
function handleMouseover(e) {
  var target = /** @type {HTMLElement} */ (e.target);
  var tagBtn = /** @type {HTMLElement | null} */ (target.closest(".flyout-tag"));
  if (!tagBtn) return;

  var flyoutEl = /** @type {HTMLElement | null} */ (tagBtn.closest(".group-flyout"));
  if (!flyoutEl) return;

  var label = tagBtn.dataset.label || "";
  if (!label) return;

  highlightTagInFlyout(flyoutEl, label);
}

/**
 * @param {MouseEvent} e
 */
function handleMouseout(e) {
  var target = /** @type {HTMLElement} */ (e.target);
  var tagBtn = /** @type {HTMLElement | null} */ (target.closest(".flyout-tag"));
  if (!tagBtn) return;

  var flyoutEl = /** @type {HTMLElement | null} */ (tagBtn.closest(".group-flyout"));
  if (!flyoutEl) return;

  clearTagHighlightsInFlyout(flyoutEl);
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Wire all event listeners on the flyout container.
 * @param {HTMLElement} container
 */
export function wireEvents(container) {
  _container = container;

  _onContainerClick = handleClick;
  _onContainerInput = handleInput;
  _onContainerKeydown = handleKeydown;
  _onContainerMousedown = handleMousedown;

  container.addEventListener("click", _onContainerClick);
  container.addEventListener("input", _onContainerInput);
  container.addEventListener("keydown", _onContainerKeydown);
  container.addEventListener("mousedown", _onContainerMousedown);
  container.addEventListener("mouseover", handleMouseover);
  container.addEventListener("mouseout", handleMouseout);

  _onDocMousemove = handleMousemove;
  _onDocMouseup = handleMouseup;
  document.addEventListener("mousemove", _onDocMousemove);
  document.addEventListener("mouseup", _onDocMouseup);
}

/**
 * Remove all wired event listeners.
 */
export function unwireEvents() {
  if (!_container) return;

  if (_onContainerClick) _container.removeEventListener("click", _onContainerClick);
  if (_onContainerInput) _container.removeEventListener("input", _onContainerInput);
  if (_onContainerKeydown) _container.removeEventListener("keydown", _onContainerKeydown);
  if (_onContainerMousedown) _container.removeEventListener("mousedown", _onContainerMousedown);
  _container.removeEventListener("mouseover", handleMouseover);
  _container.removeEventListener("mouseout", handleMouseout);

  if (_onDocMousemove) document.removeEventListener("mousemove", _onDocMousemove);
  if (_onDocMouseup) document.removeEventListener("mouseup", _onDocMouseup);

  _container = null;
  _dragInst = null;
}
