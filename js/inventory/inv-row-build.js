/* inventory/inv-row-build.js — Per-row HTML builder.
   createPartRow: creates a single inventory part row element. */

import { store, getThreshold } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { openAdjustModal, openPriceModal } from '../inventory-modals.js';
import { openFlyout } from '../group-flyout/flyout-panel.js';
import { renderPartRowHtml } from './inventory-renderer.js';
import { isFlyoutDragActive } from './inv-events.js';
import state from './inv-state.js';
import { createReverseLink } from './inv-mutations.js';
import { toggleSelection } from '../label-selection.js';

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
  });
  row.innerHTML = html;

  if (isSource) row.classList.add("linking-source");

  if (store.links.linkingMode && store.links.linkingBomRow) {
    row.classList.add("link-target");
    row.addEventListener("click", function () { createReverseLink(item); });
  }

  // Keep MPN text selectable even while a flyout is open (when the row
  // becomes draggable, dragstart from inside the MPN would otherwise
  // suppress text selection).
  var mpnEl = row.querySelector(".part-mpn");
  if (mpnEl) {
    mpnEl.addEventListener("dragstart", function (e) { e.preventDefault(); });
  }

  // A row in label mode has two checkboxes (left + right edge) sharing one key.
  // A single toggle does not re-render, so mirror the new state onto its pair.
  var checkboxes = row.querySelectorAll(".label-select-checkbox");
  checkboxes.forEach(function (cb) {
    cb.addEventListener("change", function (e) {
      e.stopPropagation();
      toggleSelection(cb.dataset.key);
      checkboxes.forEach(function (other) {
        if (other !== cb) other.checked = cb.checked;
      });
    });
  });

  var adjBtn = row.querySelector(".adj-btn");
  if (adjBtn) {
    adjBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      openAdjustModal(item);
    });
  }
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
      store.links.setLinkingMode(true, item);
    });
  }
  var gpBadge = row.querySelector(".generic-group-badge");
  if (gpBadge) {
    gpBadge.addEventListener("click", function (e) {
      e.stopPropagation();
      openFlyout(gpBadge.dataset.genericId, gpBadge);
    });
  }

  var nmBadge = row.querySelector(".near-miss-badge");
  if (nmBadge) {
    nmBadge.addEventListener("click", function (e) {
      e.stopPropagation();
      store.links.setLinkingMode(true, item);
    });
  }

  return row;
}
