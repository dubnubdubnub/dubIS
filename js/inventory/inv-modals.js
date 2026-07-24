// @ts-check
/* inv-modals.js — Adjustment and price modals for inventory parts.
   Extracted from inventory-panel.js for focused maintainability; moved under
   js/inventory/ (Task 12 split) with pricing helpers factored out to
   pricing-utils.js. */

import { api, AppLog } from '../api.js';
import { showToast, Modal, linkPriceInputs, escHtml, formatMoney } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { scheduleInventoryRefresh } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { el } from '../dom/html.js';
import { defineFormModal } from '../components/form-modal.js';
import { createFetchController } from './fetch-controller.js';

// ── Undo/redo tracking ──
let lastAdjustMeta = null;
let lastPriceMeta = null;

// ── Editable fields: JS key → display label ──
const EDITABLE_FIELDS = [
  ["lcsc", "LCSC"],
  ["digikey", "Digikey"],
  ["pololu", "Pololu"],
  ["mouser", "Mouser"],
  ["mpn", "MPN"],
  ["manufacturer", "Manufacturer"],
  ["package", "Package"],
  ["description", "Description"],
];

// ── DOM references (set in init) ──
let modalTitle;
let modalDetailTable;
let adjType;
let adjQty;
let adjNote;
let adjUnitPrice;
let adjExtPrice;
let adjFetch;
let currentPart = null;
let adjModal;
let deletePartBtn;
let deleteArmed = false;
let deletePartGroupNames = [];

// Price modal is now managed via defineFormModal — these refs are set in init()
let priceFetchController = null;
let priceFormModal = null;

function buildFieldInput(key, value, placeholder, extraClass) {
  return '<input type="text" class="modal-field-input' + (extraClass || "") + '" data-field="' + key + '" value="' + escHtml(value) + '" placeholder="' + escHtml(placeholder) + '">';
}

/**
 * @param {import('../types.js').InventoryItem} item
 */
export function openAdjustModal(item) {
  currentPart = item;
  const pk = invPartKey(item);
  modalTitle.textContent = "Adjust — " + pk;

  // Build detail rows — editable fields get inputs, read-only fields are plain text
  var html = "";
  var noDist = !item.lcsc && !item.digikey && !item.pololu && !item.mouser;
  for (var i = 0; i < EDITABLE_FIELDS.length; i++) {
    var key = EDITABLE_FIELDS[i][0];
    var label = EDITABLE_FIELDS[i][1];
    var value = item[key] || "";
    var warnClass = noDist && (key === "lcsc" || key === "digikey" || key === "pololu" || key === "mouser") ? " modal-field-warn" : "";
    if (key === "description") {
      var fetchDescDisabled = noDist ? " disabled" : "";
      html += "<tr><td>" + escHtml(label) + "</td><td>" +
        buildFieldInput(key, value, "", warnClass) +
        '<button type="button" class="fetch-desc-btn"' + fetchDescDisabled +
        ' title="Fill description from the matched distributor">Fetch description</button>' +
        "</td></tr>";
    } else {
      html += "<tr><td>" + escHtml(label) + "</td><td>" + buildFieldInput(key, value, "", warnClass) + "</td></tr>";
    }
    // Show hint after the Mouser row
    if (key === "mouser" && noDist) {
      html += '<tr><td></td><td><span class="no-dist-warn">⚠ Enter an LCSC, Digikey, Pololu, or Mouser PN</span></td></tr>';
    }
  }
  // Read-only rows
  if (item.section) html += "<tr><td>Section</td><td>" + escHtml(item.section) + "</td></tr>";
  html += "<tr><td>Qty</td><td>" + item.qty + "</td></tr>";
  if (item.unit_price > 0) html += "<tr><td>Unit Price</td><td>" + escHtml(formatMoney(item.unit_price)) + "</td></tr>";
  if (item.ext_price > 0) html += "<tr><td>Ext. Price</td><td>" + escHtml(formatMoney(item.ext_price)) + "</td></tr>";
  modalDetailTable.innerHTML = html;

  adjType.value = "set";
  adjQty.value = item.qty;
  adjNote.value = "";
  adjUnitPrice.value = item.unit_price > 0 ? item.unit_price : "";
  adjExtPrice.value = item.ext_price > 0 ? item.ext_price : "";

  adjFetch.configure(item).then(() => {
    const { canDelete, groupNames } = adjFetch.deleteEligibility();
    deleteArmed = false;
    deletePartBtn.classList.toggle("hidden", !canDelete);
    deletePartBtn.classList.remove("armed");
    deletePartBtn.textContent = "Delete part";
    deletePartGroupNames = groupNames;
  });

  adjModal.open();
  adjQty.focus();
  adjQty.select();
}

/** Collect changed fields from the detail table inputs. */
function getChangedFields() {
  var changed = {};
  var inputs = modalDetailTable.querySelectorAll(".modal-field-input");
  for (var i = 0; i < inputs.length; i++) {
    var key = inputs[i].dataset.field;
    var newVal = inputs[i].value.trim();
    var origVal = (currentPart[key] || "").trim();
    if (newVal !== origVal) changed[key] = newVal;
  }
  return changed;
}

/** Re-populate the detail-table inputs from a (possibly updated) item. */
function populateDetailFields(item) {
  var inputs = modalDetailTable.querySelectorAll(".modal-field-input");
  for (var i = 0; i < inputs.length; i++) {
    var key = inputs[i].dataset.field;
    inputs[i].value = item[key] || "";
  }
}

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

export function init() {
  // ── Adjustment Modal ──
  modalTitle = document.getElementById("modal-title");
  modalDetailTable = document.getElementById("modal-detail-table");
  adjType = document.getElementById("adj-type");
  adjQty = document.getElementById("adj-qty");
  adjNote = document.getElementById("adj-note");
  adjUnitPrice = /** @type {HTMLInputElement} */ (document.getElementById("adj-unit-price"));
  adjExtPrice = /** @type {HTMLInputElement} */ (document.getElementById("adj-ext-price"));
  deletePartBtn = /** @type {HTMLButtonElement} */ (document.getElementById("adj-delete-part"));

  adjModal = Modal("adjust-modal", {
    onClose: () => { currentPart = null; },
    cancelId: "adj-cancel",
    confirmId: "adj-apply",
  });
  linkPriceInputs(adjUnitPrice, adjExtPrice, () => currentPart ? currentPart.qty : 0);

  adjFetch = createFetchController({
    panelEl: /** @type {HTMLElement} */ (document.getElementById("adj-fetch-panel")),
    unitInput: adjUnitPrice,
    onPartUpdated: (freshItem) => {
      if (!freshItem) return;
      currentPart = freshItem;
      populateDetailFields(freshItem);
    },
  });

  modalDetailTable.addEventListener("click", (e) => {
    const btn = /** @type {HTMLElement} */ (e.target).closest(".fetch-desc-btn");
    if (!btn) return;
    if (!adjFetch.hasSourcedRows()) { showToast("No distributor PN to fetch from"); return; }
    const desc = adjFetch.bestDescription();
    if (!desc) { showToast("No description available yet — try again in a moment"); return; }
    const input = /** @type {HTMLInputElement} */ (
      modalDetailTable.querySelector('.modal-field-input[data-field="description"]')
    );
    if (!input) return;
    input.value = desc;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    showToast("Description filled — review and Apply");
  });

  deletePartBtn.addEventListener("click", async () => {
    if (!currentPart) return;
    const pk = invPartKey(currentPart);
    if (!deleteArmed) {
      deleteArmed = true;
      const suffix = deletePartGroupNames.length
        ? " Also in: " + deletePartGroupNames.join(", ")
        : "";
      deletePartBtn.textContent = "Really delete?" + suffix;
      deletePartBtn.classList.add("armed");
      return;
    }
    const result = await api("delete_part", pk);
    if (!result) return;   // api() already toasted the error
    scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
    showToast("Deleted " + pk);
    adjModal.close();
  });

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

    // Check if metadata fields changed
    const changedFields = getChangedFields();
    const fieldsChanged = Object.keys(changedFields).length > 0;

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

    var result;

    // Apply metadata field updates first
    if (fieldsChanged) {
      result = await api("update_part_fields", pk, changedFields);
      if (!result) {
        AppLog.warn("Field update failed for " + pk);
      }
    }

    // Apply qty adjustment
    const qtyResult = await api("adjust_part", type, pk, qty, note);
    if (!qtyResult) {
      UndoRedo.popLast();
      return;
    }
    result = qtyResult;

    // Apply price update if changed
    if (priceChanged) {
      const up = !isNaN(newUp) ? newUp : null;
      const ep = !isNaN(newEp) ? newEp : null;
      const priceResult = await api("update_part_price", pk, up, ep);
      if (!priceResult) {
        AppLog.warn("Qty adjusted, but price update failed for " + pk);
        UndoRedo._undo[UndoRedo._undo.length - 1].data.priceChanged = false;
        scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
        adjModal.close();
        return;
      }
      result = priceResult;
    }

    scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));

    lastAdjustMeta = {
      partKey: pk, adjType: type, qty: qty, note: note,
      priceChanged: priceChanged,
      oldUp: origUp, oldEp: origEp,
      newUp: priceChanged ? (!isNaN(newUp) ? newUp : null) : null,
      newEp: priceChanged ? (!isNaN(newEp) ? newEp : null) : null,
    };
    adjModal.close();
    var toastMsg = "Adjusted " + pk;
    if (fieldsChanged) toastMsg += " (fields updated)";
    showToast(toastMsg);
  });

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
      scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
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
      scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
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

// Module-level ref to current price modal ctx (set in the patched open()).
// Used by onInput to access the current item's qty for unit↔ext math.
let _priceCtx = null;
