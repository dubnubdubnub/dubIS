// @ts-check
/* inventory/inv-row-build.js — Per-row HTML builder + delegated row handlers.
   createPartRow: creates a single inventory part row element.
   setupRowDelegation: one-time delegated listeners on the inventory body that
   replace the old per-row addEventListener closures. */

import { store, getThreshold } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { openAdjustModal, openPriceModal } from './inv-modals.js';
import { openFlyout } from '../group-flyout/flyout-panel.js';
import { renderPartRowHtml } from './inv-html-builders.js';
import { isFlyoutDragActive } from './inv-events.js';
import state, { generationOpacityFor } from './inv-state.js';
import { createReverseLink } from './inv-mutations.js';
import { toggleSelection } from '../label-selection.js';
import { activateInlineEdit } from './inv-inline-edit.js';
import * as cartAddMode from '../cart/cart-add.js';
import { on } from '../dom/delegate.js';

// Row element → inventory item. Lets the delegated handlers below recover the
// exact item object a row was rendered from (identity matters: e.g. the
// linking-source check compares `store.links.linkingInvItem === item`).
// WeakMap so discarded rows (full re-render wipes innerHTML) don't leak.
var rowItems = new WeakMap();

export function createPartRow(item, sectionKey, sectionChip) {
  var row = document.createElement("div");
  row.className = "inv-part-row";
  // Only draggable while a generic-parts flyout is open (drop target). Off by
  // default so click-and-drag selects text instead of starting a row drag.
  row.draggable = isFlyoutDragActive();
  row.dataset.partId = invPartKey(item);

  var pk = invPartKey(item).toUpperCase();
  var nearMiss = state.nearMissMap ? state.nearMissMap.get(pk) : null;
  if (nearMiss) row.classList.add("inv-row-near-miss");

  var isSource = store.links.linkingMode && store.links.linkingInvItem === item;
  var html = renderPartRowHtml(item, {
    hideDescs: state.hideDescs,
    isBomMode: !!state.bomData,
    isLinkSource: isSource,
    isReverseTarget: false,
    sectionKey: sectionKey,
    threshold: getThreshold(sectionKey),
    genericParts: store.genericParts,
    nearMiss: nearMiss || null,
    sectionChip: sectionChip,
    importOpacity: generationOpacityFor(pk),
  });
  row.innerHTML = html;

  if (isSource) row.classList.add("linking-source");
  if (store.links.linkingMode && store.links.linkingBomRow) {
    row.classList.add("link-target");
  }

  rowItems.set(row, item);

  // Inline editing: double-click on qty / unit-price cells.
  // Guard logic is inside activateInlineEdit (link mode, flyout drag).
  activateInlineEdit(row, item);

  return row;
}

// ── Delegated row handlers ──
//
// A single set of listeners on the inventory body replaces the ~7 per-row
// addEventListener closures the old createPartRow attached. All part rows —
// normal tree, vendor piles, groups view, BOM "other inventory" — live inside
// the inventory body, so one root covers every render path.

var _delegationWired = false;

/**
 * Wire the delegated row listeners once. Called from inventory-panel init()
 * after state.body exists. Idempotent.
 */
export function setupRowDelegation(root) {
  if (_delegationWired) return;
  _delegationWired = true;

  // Single delegated click listener. The branches mirror the old per-element
  // listeners: each button/badge called e.stopPropagation() so the row-level
  // click handler never fired for them — replicated here by handling the most
  // specific target first and returning. stopPropagation() is kept so the
  // event does not reach document-level listeners (popover close, fan-stack),
  // exactly as before.
  on(root, "click", ".inv-part-row", function (e, rowEl) {
    var item = rowItems.get(rowEl);
    if (!item) return; // row not built by createPartRow (shouldn't happen)
    var target = /** @type {Element} */ (e.target);

    var control = /** @type {HTMLElement|null} */ (target.closest(
      ".adj-btn, .price-warn-btn, .no-dist-warn, .link-btn, .generic-group-badge, .near-miss-badge"
    ));
    if (control && rowEl.contains(control)) {
      e.stopPropagation();
      if (control.classList.contains("adj-btn") || control.classList.contains("no-dist-warn")) {
        openAdjustModal(item);
      } else if (control.classList.contains("price-warn-btn")) {
        openPriceModal(item);
      } else if (control.classList.contains("generic-group-badge")) {
        openFlyout(control.dataset.genericId, control);
      } else {
        // .link-btn and .near-miss-badge both arm forward-linking mode.
        store.links.setLinkingMode(true, item);
      }
      return;
    }

    // Cart-add mode (checked first, unconditionally): a click anywhere on the
    // row that isn't one of the controls above adds the part to the active
    // cart instead of falling through to the reverse-link-target behavior.
    if (cartAddMode.handleRowClick(item)) return;
    if (store.links.linkingMode && store.links.linkingBomRow) createReverseLink(item);
  });

  // A row in label mode has two checkboxes (left + right edge) sharing one key.
  // A single toggle does not re-render, so mirror the new state onto its pair.
  on(root, "change", ".inv-part-row .label-select-checkbox", function (e, matched) {
    e.stopPropagation();
    var cb = /** @type {HTMLInputElement} */ (matched);
    toggleSelection(cb.dataset.key);
    var rowEl = cb.closest(".inv-part-row");
    var pair = /** @type {NodeListOf<HTMLInputElement>} */ (rowEl.querySelectorAll(".label-select-checkbox"));
    pair.forEach(function (other) {
      if (other !== cb) other.checked = cb.checked;
    });
  });

  // Keep MPN text selectable even while a flyout is open (when rows become
  // draggable, dragstart from inside the MPN would otherwise suppress text
  // selection).
  on(root, "dragstart", ".inv-part-row .part-mpn", function (e) {
    e.preventDefault();
  });
}
