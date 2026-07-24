// @ts-check
/* price-modal.js — Price modal for inventory parts.
   Split from inv-modals.js (Task 3) so adjust and price modals can be
   maintained independently; inv-modals.js remains a thin barrel. */

import { api, AppLog } from '../api.js';
import { showToast } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { scheduleInventoryRefresh } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { el } from '../dom/html.js';
import { defineFormModal } from '../components/form-modal.js';
import { createFetchController } from './fetch-controller.js';

// ── Undo/redo tracking ──
let lastPriceMeta = null;

// Price modal is now managed via defineFormModal — these refs are set in init()
let priceFetchController = null;
let priceFormModal = null;

// Module-level ref to current price modal ctx (set in the patched open()).
// Used by onInput to access the current item's qty for unit↔ext math.
let _priceCtx = null;

/**
 * Open the price modal for the given inventory item.
 * The modal is built once (via defineFormModal) and reused.
 *
 * @param {import('../types.js').InventoryItem} item
 */
export function openPriceModal(item) {
  if (!priceFormModal) throw new Error("inv-modals: init() not called before openPriceModal()");
  priceFormModal.open(item);
}

export function initPriceModal() {
  // ── Price Modal (built via defineFormModal) ──
  //
  // defineFormModal creates the overlay + .modal DOM dynamically and wires
  // Modal() for backdrop/Esc/Enter/focus-trap. We then inject the fetch-price
  // controls (supplier dropdown, fetch button, tier chips) between the last
  // form row and the action buttons — matching the original markup structure.

  priceFormModal = defineFormModal("price-modal", {
    title: (item) => invPartKey(item) + (item.mpn && item.lcsc ? " — " + item.mpn : ""),
    subtitle: (item) => (item.description || item.package || "") + " (qty: " + item.qty + ")",

    confirmId: "price-apply",
    cancelId: "price-cancel",

    fields: [
      {
        key: "unit",
        label: "Unit Price ($):",
        type: "number",
        attrs: { id: "price-unit", min: "0", step: "0.01" },
      },
      {
        key: "ext",
        label: "Ext. Price ($):",
        type: "number",
        attrs: { id: "price-ext", min: "0", step: "0.01" },
      },
    ],

    onPopulate: (item) => ({
      unit: item.unit_price > 0 ? String(item.unit_price) : "",
      ext:  item.ext_price  > 0 ? String(item.ext_price)  : "",
    }),

    // Unit↔ext price linkage: mirrors the existing linkPriceInputs() math.
    onInput: (key, values, setValue) => {
      // We need the current part's qty, but onInput doesn't receive ctx directly.
      // Access the last opened item via the closure captured in openPriceModal → open(item).
      // The overlay's data is managed by the form-modal; we reach qty via the DOM title.
      // Instead, wire qty by reading it from the subtitle text — fragile.
      // Better: capture qty in a closure via priceFetchController.
      // For now, read from the stored pricePart reference maintained in undo/snapshot.
      // Actually, the cleanest approach is: we keep a local ref updated on each open()
      // which happens just before onInput could fire. See _priceCtx below.
      const qty = _priceCtx ? _priceCtx.qty : 0;
      if (key === "unit") {
        const up = parseFloat(values.unit);
        if (!isNaN(up) && qty > 0) setValue("ext", (up * qty).toFixed(2));
      } else if (key === "ext") {
        const ep = parseFloat(values.ext);
        if (!isNaN(ep) && qty > 0) setValue("unit", (ep / qty).toFixed(4));
      }
    },

    validate: (values) => {
      const up = parseFloat(values.unit);
      const ep = parseFloat(values.ext);
      if (isNaN(up) && isNaN(ep)) {
        // Use a toast for this (matching original behavior), not inline error.
        showToast("Enter a unit or ext price");
        // Return a non-null errors object so confirm is blocked, but with no inline message.
        return { unit: "" };
      }
      return null;
    },

    onConfirm: async (values, item) => {
      const pk = invPartKey(item);
      const rawUp = parseFloat(values.unit);
      const up = isNaN(rawUp) ? null : rawUp;
      const rawEp = parseFloat(values.ext);
      const ep = isNaN(rawEp) ? null : rawEp;

      const result = await api("update_part_price", pk, up, ep);
      if (!result) return null;

      lastPriceMeta = {
        partKey: pk,
        oldUp: item.unit_price || 0,
        oldEp: item.ext_price  || 0,
        newUp: up,
        newEp: ep,
      };
      scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
      return result;
    },

    undo: {
      type: "price",
      snapshot: (item, values) => {
        const rawUp = parseFloat(values.unit);
        const rawEp = parseFloat(values.ext);
        return {
          _undoType: "price",
          partKey: invPartKey(item),
          oldUp: item.unit_price || 0,
          oldEp: item.ext_price  || 0,
          newUp: isNaN(rawUp) ? null : rawUp,
          newEp: isNaN(rawEp) ? null : rawEp,
        };
      },
      restore: async () => { /* handled by UndoRedo.register("price") below */ },
    },

    confirmLabel: "Save",

    successToast: (_values, item) => "Price updated for " + invPartKey(item),
  });

  // ── Multi-distributor fetch panel for the price modal ────────────────────
  // Inject a single panel container before the action buttons.
  const priceModalInner = priceFormModal.el.querySelector(".modal");
  const priceActionsEl  = priceFormModal.el.querySelector(".modal-actions");

  const priceFetchPanel = el("div", { id: "price-fetch-panel", class: "fetch-panel hidden" });
  priceModalInner.insertBefore(priceFetchPanel, priceActionsEl);

  const priceUnitInputEl = /** @type {HTMLInputElement} */ (document.getElementById("price-unit"));
  priceFetchController = createFetchController({
    panelEl:   priceFetchPanel,
    unitInput: priceUnitInputEl,
  });

  // Patch openPriceModal to also configure the fetch controller after form-modal opens.
  // We do this by wrapping the open() call: priceFormModal.open() already fires onPopulate
  // and sets field values; we then call priceFetchController.configure(item).
  const _originalOpen = priceFormModal.open.bind(priceFormModal);
  priceFormModal.open = (item) => {
    _priceCtx = item;
    _originalOpen(item);
    priceFetchController.configure(item);
  };

  // ── Undo/Redo handler for price updates ──

  UndoRedo.register("price", async (action, data) => {
    if (action === "snapshot") {
      if (lastPriceMeta) {
        return { _undoType: "price-done", ...lastPriceMeta };
      }
      return { _undoType: "price-none" };
    }
    if (data._undoType === "price") {
      const result = await api("update_part_price", data.partKey, data.oldUp, data.oldEp);
      if (!result) throw new Error("Failed to undo price update");
      lastPriceMeta = null;
      scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
      showToast("Undid price update for " + data.partKey);
    } else if (data._undoType === "price-done") {
      const result = await api("update_part_price", data.partKey, data.newUp, data.newEp);
      if (!result) throw new Error("Failed to redo price update");
      lastPriceMeta = { ...data };
      delete lastPriceMeta._undoType;
      scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
      showToast("Redid price update for " + data.partKey);
    }
  });
}
