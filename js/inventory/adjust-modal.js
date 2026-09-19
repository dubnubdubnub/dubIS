// @ts-check
/* adjust-modal.js — Adjustment modal for inventory parts.
   Split from inv-modals.js (Task 3) so adjust and price modals can be
   maintained independently; inv-modals.js remains a thin barrel. */

import { apiOn, AppLog } from '../api.js';
import { showToast, Modal, linkPriceInputs, escHtml, formatMoney } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { scheduleInventoryRefresh } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { createFetchController } from './fetch-controller.js';
import { sourceEntries, writeTarget, viewStateFrom } from './inv-source-logic.js';
import { sourceStatusSignal } from '../signals.js';

// ── Undo/redo tracking ──
let lastAdjustMeta = null;

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
  // ── Which server this adjustment lands on ──
  //
  // Only present in a merged view, and only interactive when the row is
  // genuinely ambiguous. A part with 850 on the bench and 400 in the shop has
  // no "the" server: writing to the bigger one, or the first one, would move
  // stock on a machine the user was not thinking about — and the hub refuses
  // `merged` as a write target for exactly that reason (400,
  // server/sources.py). So the modal asks, and Apply refuses until it is told.
  var srcEntries = sourceEntries(item);
  // A degraded view forces the chooser open even for a row that names ONE
  // server, because "one server" then means "one server answered": the part may
  // also be stocked on the one that is asleep, and this row cannot tell. Naming
  // it flatly would be the modal asserting something the fetch never
  // established.
  var srcView = viewStateFrom(sourceStatusSignal.peek());
  if (srcEntries.length === 1 && !srcView.degraded) {
    html += "<tr><td>Server</td><td>" + escHtml(srcEntries[0].name) + "</td></tr>";
  } else if (srcEntries.length > 1 || (srcView.degraded && srcEntries.length)) {
    var opts = '<option value="">Choose a server…</option>' + srcEntries.map(function (s) {
      return '<option value="' + escHtml(s.id) + '">' + escHtml(s.name) +
        " — " + s.qty + " here</option>";
    }).join("");
    // NOT `.modal-field-input`: getChangedFields()/populateDetailFields() walk
    // that class and read `dataset.field` off each hit, so a control with no
    // `data-field` would post itself as a field update named `undefined`.
    html += '<tr><td>Server</td><td><select class="adj-source-select">' +
      opts + "</select>" +
      (srcView.degraded
        ? '<div class="adj-source-warn">\u26A0 ' +
            escHtml(srcView.missing.join(', ') || 'A server') +
            ' did not answer \u2014 this part may be stocked there too</div>'
        : "") +
      "</td></tr>";
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

export function initAdjustModal() {
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
    // Deleting a part that lives on two servers is the same ambiguity an adjust
    // has, with a worse blast radius, so it asks the same question.
    const delTarget = writeTarget(currentPart, (
      /** @type {HTMLSelectElement|null} */ (modalDetailTable.querySelector(".adj-source-select"))
      || { value: "" }
    ).value, viewStateFrom(sourceStatusSignal.peek()));
    if (!delTarget.ok) { showToast(delTarget.reason); return; }
    const result = await apiOn(delTarget.sourceId, "delete_part", pk);
    if (!result) return;   // apiOn() already toasted the error
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

    // Where this write goes. Resolved BEFORE anything is sent and before undo
    // state is saved: a refusal here must leave the modal exactly as the user
    // left it, with nothing half-applied and nothing to undo.
    const chooser = /** @type {HTMLSelectElement|null} */ (
      modalDetailTable.querySelector(".adj-source-select")
    );
    // Re-read rather than reusing the value from open time: a refresh can land
    // while the modal is open, and the state that matters is the one the write
    // is being made from.
    const target = writeTarget(
      currentPart, chooser ? chooser.value : "", viewStateFrom(sourceStatusSignal.peek()),
    );
    if (!target.ok) {
      showToast(target.reason);
      if (chooser) chooser.focus();
      return;
    }
    const src = target.sourceId;

    // Check if metadata fields changed
    const changedFields = getChangedFields();
    const fieldsChanged = Object.keys(changedFields).length > 0;

    // Save undo state
    UndoRedo.save("adjust", {
      _undoType: "adjust",
      partKey: pk,
      // The server this landed on, so undo and redo go back to the same one.
      // Undoing an adjustment on a DIFFERENT server than it was made on would
      // silently move stock twice, in opposite directions, on two machines.
      sourceId: src,
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
      result = await apiOn(src, "update_part_fields", pk, changedFields);
      if (!result) {
        AppLog.warn("Field update failed for " + pk);
      }
    }

    // Apply qty adjustment
    const qtyResult = await apiOn(src, "adjust_part", type, pk, qty, note);
    if (!qtyResult) {
      UndoRedo.popLast();
      return;
    }
    result = qtyResult;

    // Apply price update if changed
    if (priceChanged) {
      const up = !isNaN(newUp) ? newUp : null;
      const ep = !isNaN(newEp) ? newEp : null;
      const priceResult = await apiOn(src, "update_part_price", pk, up, ep);
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
      partKey: pk, sourceId: src, adjType: type, qty: qty, note: note,
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

  // ── Undo/Redo handler for inventory adjustments ──

  UndoRedo.register("adjust", async (action, data) => {
    if (action === "snapshot") {
      if (lastAdjustMeta) {
        return { _undoType: "adjust-done", ...lastAdjustMeta };
      }
      return { _undoType: "adjust-none" };
    }
    if (data._undoType === "adjust") {
      const fresh = await apiOn(data.sourceId, "remove_last_adjustments", 1);
      if (!fresh) throw new Error("Failed to undo adjustment");
      let result = fresh;
      if (data.priceChanged) {
        result = await apiOn(data.sourceId, "update_part_price", data.partKey, data.oldUp, data.oldEp);
        if (!result) throw new Error("Failed to undo price change");
      }
      lastAdjustMeta = null;
      scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
      showToast("Undid adjustment for " + data.partKey);
    } else if (data._undoType === "adjust-done") {
      const qtyResult = await apiOn(data.sourceId, "adjust_part", data.adjType, data.partKey, data.qty, data.note);
      if (!qtyResult) throw new Error("Failed to redo adjustment");
      let result = qtyResult;
      if (data.priceChanged) {
        result = await apiOn(data.sourceId, "update_part_price", data.partKey, data.newUp, data.newEp);
        if (!result) throw new Error("Failed to redo price change");
      }
      lastAdjustMeta = { ...data };
      delete lastAdjustMeta._undoType;
      scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
      showToast("Redid adjustment for " + data.partKey);
    }
  });
}
