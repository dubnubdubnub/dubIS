/* inventory-modals.js — Adjustment and price modals for inventory parts.
   Extracted from inventory-panel.js for focused maintainability. */

import { api, AppLog } from './api.js';
import { showToast, Modal, linkPriceInputs } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { onInventoryUpdated } from './store.js';
import { invPartKey } from './part-keys.js';

// ── Undo/redo tracking ──
let lastAdjustMeta = null;
let lastPriceMeta = null;

// ── Adjustment Modal ──
const modalTitle = document.getElementById("modal-title");
const modalSubtitle = document.getElementById("modal-subtitle");
const modalQty = document.getElementById("modal-current-qty");
const adjType = document.getElementById("adj-type");
const adjQty = document.getElementById("adj-qty");
const adjNote = document.getElementById("adj-note");
const adjUnitPrice = document.getElementById("adj-unit-price");
const adjExtPrice = document.getElementById("adj-ext-price");
let currentPart = null;

const adjModal = Modal("adjust-modal", {
  onClose: () => { currentPart = null; },
  cancelId: "adj-cancel",
});
linkPriceInputs(adjUnitPrice, adjExtPrice, () => currentPart ? currentPart.qty : 0);

export function openAdjustModal(item) {
  currentPart = item;
  const pk = invPartKey(item);
  modalTitle.textContent = pk + (item.mpn && item.lcsc ? " — " + item.mpn : "");
  modalSubtitle.textContent = item.description || item.package || "";
  modalQty.textContent = "Current qty: " + item.qty;
  adjType.value = "set";
  adjQty.value = item.qty;
  adjNote.value = "";
  adjUnitPrice.value = item.unit_price > 0 ? item.unit_price : "";
  adjExtPrice.value = item.ext_price > 0 ? item.ext_price : "";
  adjModal.open();
  adjQty.focus();
  adjQty.select();
}

document.getElementById("adj-apply").addEventListener("click", async () => {
  if (!currentPart) { AppLog.warn("No part selected for adjustment"); return; }
  const pk = invPartKey(currentPart);
  const type = adjType.value;
  const qty = parseInt(adjQty.value, 10) || 0;
  const note = adjNote.value;

  // Check if price changed
  const newUp = parseFloat(adjUnitPrice.value);
  const newEp = parseFloat(adjExtPrice.value);
  const origUp = currentPart.unit_price || 0;
  const origEp = currentPart.ext_price || 0;
  const priceChanged = (!isNaN(newUp) && newUp !== origUp) || (!isNaN(newEp) && newEp !== origEp);

  // Save undo state
  UndoRedo.save("adjust", {
    _undoType: "adjust",
    partKey: pk,
    adjType: type,
    qty: qty,
    note: note,
    priceChanged: priceChanged,
    oldUp: origUp,
    oldEp: origEp,
    newUp: priceChanged ? (!isNaN(newUp) ? newUp : null) : null,
    newEp: priceChanged ? (!isNaN(newEp) ? newEp : null) : null,
  });

  // Apply qty adjustment
  const qtyResult = await api("adjust_part", type, pk, qty, note);
  if (!qtyResult) {
    UndoRedo.popLast();
    return;
  }

  // Apply price update if changed
  if (priceChanged) {
    const up = !isNaN(newUp) ? newUp : null;
    const ep = !isNaN(newEp) ? newEp : null;
    const priceResult = await api("update_part_price", pk, up, ep);
    if (!priceResult) {
      AppLog.warn("Qty adjusted, but price update failed for " + pk);
      UndoRedo._undo[UndoRedo._undo.length - 1].data.priceChanged = false;
      onInventoryUpdated(qtyResult);
      adjModal.close();
      return;
    }
    onInventoryUpdated(priceResult);
  } else {
    onInventoryUpdated(qtyResult);
  }

  lastAdjustMeta = {
    partKey: pk, adjType: type, qty: qty, note: note,
    priceChanged: priceChanged,
    oldUp: origUp, oldEp: origEp,
    newUp: priceChanged ? (!isNaN(newUp) ? newUp : null) : null,
    newEp: priceChanged ? (!isNaN(newEp) ? newEp : null) : null,
  };
  adjModal.close();
  showToast("Adjusted " + pk);
});

document.addEventListener("keydown", (e) => {
  if (adjModal.el.classList.contains("hidden")) return;
  if (e.key === "Enter" && document.activeElement !== adjNote) {
    document.getElementById("adj-apply").click();
  }
});

// ── Price Modal ──
const priceTitle = document.getElementById("price-modal-title");
const priceSubtitle = document.getElementById("price-modal-subtitle");
const priceUnitInput = document.getElementById("price-unit");
const priceExtInput = document.getElementById("price-ext");
let pricePart = null;

const priceModal = Modal("price-modal", {
  onClose: () => { pricePart = null; },
  cancelId: "price-cancel",
});
linkPriceInputs(priceUnitInput, priceExtInput, () => pricePart ? pricePart.qty : 0);

export function openPriceModal(item) {
  pricePart = item;
  const pk = invPartKey(item);
  priceTitle.textContent = pk + (item.mpn && item.lcsc ? " — " + item.mpn : "");
  priceSubtitle.textContent = (item.description || item.package || "") + " (qty: " + item.qty + ")";
  priceUnitInput.value = item.unit_price > 0 ? item.unit_price : "";
  priceExtInput.value = item.ext_price > 0 ? item.ext_price : "";
  priceModal.open();
  priceUnitInput.focus();
}

document.getElementById("price-apply").addEventListener("click", async () => {
  if (!pricePart) { AppLog.warn("No part selected for price update"); return; }
  const pk = invPartKey(pricePart);
  const rawUp = parseFloat(priceUnitInput.value);
  const up = isNaN(rawUp) ? null : rawUp;
  const rawEp = parseFloat(priceExtInput.value);
  const ep = isNaN(rawEp) ? null : rawEp;
  if (up === null && ep === null) {
    showToast("Enter a unit or ext price");
    return;
  }

  // Save undo state
  UndoRedo.save("price", {
    _undoType: "price",
    partKey: pk,
    oldUp: pricePart.unit_price || 0,
    oldEp: pricePart.ext_price || 0,
    newUp: up,
    newEp: ep,
  });

  const fresh = await api("update_part_price", pk, up, ep);
  if (!fresh) {
    UndoRedo.popLast();
    return;
  }
  lastPriceMeta = {
    partKey: pk,
    oldUp: pricePart.unit_price || 0,
    oldEp: pricePart.ext_price || 0,
    newUp: up,
    newEp: ep,
  };
  priceModal.close();
  onInventoryUpdated(fresh);
  showToast("Price updated for " + pk);
});

document.addEventListener("keydown", (e) => {
  if (priceModal.el.classList.contains("hidden")) return;
  if (e.key === "Enter") document.getElementById("price-apply").click();
});

// ── Undo/Redo handlers for inventory mutations ──

UndoRedo.register("adjust", async (action, data) => {
  if (action === "snapshot") {
    if (lastAdjustMeta) {
      return { _undoType: "adjust-done", ...lastAdjustMeta };
    }
    return { _undoType: "adjust-none" };
  }
  if (data._undoType === "adjust") {
    const fresh = await api("remove_last_adjustments", 1);
    if (!fresh) throw new Error("Failed to undo adjustment");
    let result = fresh;
    if (data.priceChanged) {
      result = await api("update_part_price", data.partKey, data.oldUp, data.oldEp);
      if (!result) throw new Error("Failed to undo price change");
    }
    lastAdjustMeta = null;
    onInventoryUpdated(result);
    showToast("Undid adjustment for " + data.partKey);
  } else if (data._undoType === "adjust-done") {
    const qtyResult = await api("adjust_part", data.adjType, data.partKey, data.qty, data.note);
    if (!qtyResult) throw new Error("Failed to redo adjustment");
    let result = qtyResult;
    if (data.priceChanged) {
      result = await api("update_part_price", data.partKey, data.newUp, data.newEp);
      if (!result) throw new Error("Failed to redo price change");
    }
    lastAdjustMeta = { ...data };
    delete lastAdjustMeta._undoType;
    onInventoryUpdated(result);
    showToast("Redid adjustment for " + data.partKey);
  }
});

UndoRedo.register("price", async (action, data) => {
  if (action === "snapshot") {
    if (lastPriceMeta) {
      return { _undoType: "price-done", ...lastPriceMeta };
    }
    return { _undoType: "price-none" };
  }
  if (data._undoType === "price") {
    const fresh = await api("update_part_price", data.partKey, data.oldUp, data.oldEp);
    if (!fresh) throw new Error("Failed to undo price update");
    lastPriceMeta = null;
    onInventoryUpdated(fresh);
    showToast("Undid price update for " + data.partKey);
  } else if (data._undoType === "price-done") {
    const fresh = await api("update_part_price", data.partKey, data.newUp, data.newEp);
    if (!fresh) throw new Error("Failed to redo price update");
    lastPriceMeta = { ...data };
    delete lastPriceMeta._undoType;
    onInventoryUpdated(fresh);
    showToast("Redid price update for " + data.partKey);
  }
});
