// @ts-check
/* inventory-modals.js — Adjustment and price modals for inventory parts.
   Extracted from inventory-panel.js for focused maintainability. */

import { api, AppLog } from './api.js';
import { showToast, Modal, linkPriceInputs, escHtml } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { onInventoryUpdated } from './store.js';
import { invPartKey } from './part-keys.js';

// ── Undo/redo tracking ──
let lastAdjustMeta = null;
let lastPriceMeta = null;

// ── Fetch-price suppliers: item key → label + backend method ──
const FETCH_SUPPLIERS = [
  { key: "lcsc", label: "LCSC", method: "fetch_lcsc_product" },
  { key: "digikey", label: "Digikey", method: "fetch_digikey_product" },
  { key: "mouser", label: "Mouser", method: "fetch_mouser_product" },
  { key: "pololu", label: "Pololu", method: "fetch_pololu_product" },
];

/** Pick the price-break tier matching a target quantity: the tier with the
 *  largest qty that is <= targetQty. Falls back to the lowest-qty tier when
 *  targetQty is missing/<=0 or no tier qualifies. Returns a tier or null. */
export function pickTier(prices, targetQty) {
  if (!Array.isArray(prices) || prices.length === 0) return null;
  const sorted = prices.slice().sort((a, b) => a.qty - b.qty);
  let chosen = sorted[0];
  if (typeof targetQty === "number" && targetQty > 0) {
    for (let i = 0; i < sorted.length; i++) {
      if (sorted[i].qty <= targetQty) chosen = sorted[i];
    }
  }
  return chosen;
}

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

let priceTitle;
let priceSubtitle;
let priceUnitInput;
let priceExtInput;
let priceFetch;
let pricePart = null;
let priceModal;

function buildFieldInput(key, value, placeholder, extraClass) {
  return '<input type="text" class="modal-field-input' + (extraClass || "") + '" data-field="' + key + '" value="' + escHtml(value) + '" placeholder="' + escHtml(placeholder) + '">';
}

/**
 * @param {import('./types.js').InventoryItem} item
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
    html += "<tr><td>" + escHtml(label) + "</td><td>" + buildFieldInput(key, value, "", warnClass) + "</td></tr>";
    // Show hint after the Mouser row
    if (key === "mouser" && noDist) {
      html += '<tr><td></td><td><span class="no-dist-warn">\u26A0 Enter an LCSC, Digikey, Pololu, or Mouser PN</span></td></tr>';
    }
  }
  // Read-only rows
  if (item.section) html += "<tr><td>Section</td><td>" + escHtml(item.section) + "</td></tr>";
  html += "<tr><td>Qty</td><td>" + item.qty + "</td></tr>";
  if (item.unit_price > 0) html += "<tr><td>Unit Price</td><td>$" + escHtml(item.unit_price.toFixed(2)) + "</td></tr>";
  if (item.ext_price > 0) html += "<tr><td>Ext. Price</td><td>$" + escHtml(item.ext_price.toFixed(2)) + "</td></tr>";
  modalDetailTable.innerHTML = html;

  adjType.value = "set";
  adjQty.value = item.qty;
  adjNote.value = "";
  adjUnitPrice.value = item.unit_price > 0 ? item.unit_price : "";
  adjExtPrice.value = item.ext_price > 0 ? item.ext_price : "";

  adjFetch.configure(item);

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

/**
 * Wire the "Fetch current price" controls shared by the Adjust and Price modals.
 * Registers the button/tier click handlers once; call the returned `configure(part)`
 * each time a modal opens to set up the supplier dropdown and reset the tier list.
 *
 * @param {{supplierSelect: HTMLSelectElement, fetchBtn: HTMLButtonElement,
 *          tiersEl: HTMLElement, unitInput: HTMLInputElement}} els
 */
function createFetchController({ supplierSelect, fetchBtn, tiersEl, unitInput }) {
  let part = null;

  // Set Unit Price and trigger the existing Ext recompute (linkPriceInputs
  // listens on the "input" event).
  function setUnitPrice(price) {
    unitInput.value = price;
    unitInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // Render all price-break tiers as clickable chips; highlight the selected one.
  function renderTiers(prices, selectedTier) {
    const sorted = prices.slice().sort((a, b) => a.qty - b.qty);
    tiersEl.innerHTML = sorted.map(t => {
      const sel = selectedTier && t.qty === selectedTier.qty && t.price === selectedTier.price ? " selected" : "";
      return '<button type="button" class="fetch-tier' + sel + '" data-qty="' + Number(t.qty) +
        '" data-price="' + Number(t.price) + '">' + escHtml(String(t.qty)) + '+ → $' +
        escHtml(Number(t.price).toFixed(4)) + '</button>';
    }).join("");
    tiersEl.classList.remove("hidden");
  }

  fetchBtn.addEventListener("click", async () => {
    if (!part) { AppLog.warn("No part selected for price fetch"); return; }
    const sources = FETCH_SUPPLIERS.filter(s => (part[s.key] || "").trim());
    if (sources.length === 0) return;
    // selected supplier: dropdown value when visible/multiple, else the single source
    let supplier = sources[0];
    if (!supplierSelect.classList.contains("hidden") && supplierSelect.value) {
      supplier = FETCH_SUPPLIERS.find(s => s.key === supplierSelect.value) || supplier;
    }
    const partNumber = (part[supplier.key] || "").trim();
    const pk = invPartKey(part);
    const origText = fetchBtn.textContent;
    fetchBtn.disabled = true;
    fetchBtn.textContent = "Fetching…";
    try {
      const product = await api(supplier.method, partNumber);
      if (!product || !Array.isArray(product.prices) || product.prices.length === 0) {
        showToast("Couldn't fetch price for " + supplier.label);
        return;
      }
      const lastPoQty = await api("get_last_po_quantity", pk);
      const tier = pickTier(product.prices, typeof lastPoQty === "number" ? lastPoQty : null);
      if (tier) setUnitPrice(tier.price);
      renderTiers(product.prices, tier);
      // fire-and-forget price-history logging
      api("record_fetched_prices", pk, supplier.key, product.prices).catch(() => { /* ignore */ });
    } finally {
      fetchBtn.disabled = false;
      fetchBtn.textContent = origText;
    }
  });

  tiersEl.addEventListener("click", (e) => {
    const row = /** @type {HTMLElement|null} */ (/** @type {Element} */ (e.target).closest(".fetch-tier"));
    if (!row) return;
    setUnitPrice(Number(row.dataset.price));
    tiersEl.querySelectorAll(".fetch-tier").forEach(r => r.classList.remove("selected"));
    row.classList.add("selected");
  });

  /** Set up the controls for a newly opened modal: pick/show suppliers, reset tiers. */
  function configure(p) {
    part = p;
    tiersEl.innerHTML = "";
    tiersEl.classList.add("hidden");
    const sources = FETCH_SUPPLIERS.filter(s => (p[s.key] || "").trim());
    if (sources.length <= 1) {
      fetchBtn.disabled = sources.length === 0;
      fetchBtn.title = sources.length === 0 ? "No distributor part number" : "";
      supplierSelect.innerHTML = "";
      supplierSelect.classList.add("hidden");
    } else {
      fetchBtn.disabled = false;
      fetchBtn.title = "";
      supplierSelect.innerHTML = sources.map(s =>
        '<option value="' + escHtml(s.key) + '">' + escHtml(s.label) + '</option>').join("");
      supplierSelect.classList.remove("hidden");
    }
  }

  return { configure };
}

/**
 * @param {import('./types.js').InventoryItem} item
 */
export function openPriceModal(item) {
  pricePart = item;
  const pk = invPartKey(item);
  priceTitle.textContent = pk + (item.mpn && item.lcsc ? " — " + item.mpn : "");
  priceSubtitle.textContent = (item.description || item.package || "") + " (qty: " + item.qty + ")";
  priceUnitInput.value = item.unit_price > 0 ? item.unit_price : "";
  priceExtInput.value = item.ext_price > 0 ? item.ext_price : "";
  priceFetch.configure(item);
  priceModal.open();
  priceUnitInput.focus();
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

  adjModal = Modal("adjust-modal", {
    onClose: () => { currentPart = null; },
    cancelId: "adj-cancel",
    confirmId: "adj-apply",
  });
  linkPriceInputs(adjUnitPrice, adjExtPrice, () => currentPart ? currentPart.qty : 0);

  adjFetch = createFetchController({
    supplierSelect: /** @type {HTMLSelectElement} */ (document.getElementById("adj-fetch-supplier")),
    fetchBtn: /** @type {HTMLButtonElement} */ (document.getElementById("adj-fetch-price")),
    tiersEl: document.getElementById("adj-fetch-tiers"),
    unitInput: adjUnitPrice,
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
        onInventoryUpdated(result);
        adjModal.close();
        return;
      }
      result = priceResult;
    }

    onInventoryUpdated(result);

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

  // ── Price Modal ──
  priceTitle = document.getElementById("price-modal-title");
  priceSubtitle = document.getElementById("price-modal-subtitle");
  priceUnitInput = /** @type {HTMLInputElement} */ (document.getElementById("price-unit"));
  priceExtInput = /** @type {HTMLInputElement} */ (document.getElementById("price-ext"));

  priceModal = Modal("price-modal", {
    onClose: () => { pricePart = null; },
    cancelId: "price-cancel",
    confirmId: "price-apply",
  });
  linkPriceInputs(priceUnitInput, priceExtInput, () => pricePart ? pricePart.qty : 0);

  priceFetch = createFetchController({
    supplierSelect: /** @type {HTMLSelectElement} */ (document.getElementById("price-fetch-supplier")),
    fetchBtn: /** @type {HTMLButtonElement} */ (document.getElementById("price-fetch-price")),
    tiersEl: document.getElementById("price-fetch-tiers"),
    unitInput: priceUnitInput,
  });

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
}
