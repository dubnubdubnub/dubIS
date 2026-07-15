// @ts-check
/* inventory-modals.js — Adjustment and price modals for inventory parts.
   Extracted from inventory-panel.js for focused maintainability. */

import { api, AppLog } from './api.js';
import { showToast, Modal, linkPriceInputs, escHtml } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { onInventoryUpdated } from './store.js';
import { invPartKey } from './part-keys.js';
import { el } from './dom/html.js';
import { defineFormModal } from './components/form-modal.js';
import { pickBestDescription } from './inventory/pick-description.js';

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

/** Resolve a distributor row's price at a target quantity.
 *  Returns the chosen tier plus unit + extended (unit × qty) price,
 *  or all-null when there are no usable price tiers. */
export function rowPrice(prices, qty) {
  const tier = pickTier(prices, qty);
  if (!tier) return { tier: null, unitPrice: null, extPrice: null };
  return { tier, unitPrice: tier.price, extPrice: tier.price * qty };
}

/** Index of the cheapest row by unitPrice (ties → lowest index).
 *  Rows whose unitPrice is not a finite number are ignored. Returns -1
 *  when no row has a usable price. */
export function cheapestRow(rows) {
  let best = -1;
  let bestPrice = Infinity;
  for (let i = 0; i < rows.length; i++) {
    const p = rows[i] && rows[i].unitPrice;
    if (typeof p === "number" && isFinite(p) && p < bestPrice) {
      bestPrice = p;
      best = i;
    }
  }
  return best;
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
  if (item.unit_price > 0) html += "<tr><td>Unit Price</td><td>$" + escHtml(item.unit_price.toFixed(2)) + "</td></tr>";
  if (item.ext_price > 0) html += "<tr><td>Ext. Price</td><td>$" + escHtml(item.ext_price.toFixed(2)) + "</td></tr>";
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
 * Wire the multi-distributor "current price" panel shared by the Adjust and
 * Price modals. Renders one row per distributor the part was sourced from
 * (union of record PNs + purchase-ledger PNs, from get_sourced_distributors),
 * auto-fetches every row's price concurrently on open, and feeds the cheapest
 * row's unit price into `unitInput` (overridable by clicking a row).
 *
 * @param {{panelEl: HTMLElement, unitInput: HTMLInputElement, onPartUpdated?: (freshItem: import('./types.js').InventoryItem|null) => void}} els
 */
function createFetchController({ panelEl, unitInput, onPartUpdated }) {
  /** @type {Array<{distributor:string,label:string,method:string,partNumber:string,
   *   qty:number,prices:Array<{qty:number,price:number}>|null,
   *   unitPrice:number|null,extPrice:number|null,error:string,
   *   armed:boolean,editing:boolean,description:string}>} */
  let rows = [];
  let pinnedIndex = -1;
  let pk = "";
  let lastGroupNames = [];
  let lastHasPurchaseHistory = false;

  function setUnitPrice(price) {
    unitInput.value = price;
    unitInput.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function fmt(n) {
    return "$" + Number(n).toFixed(4);
  }

  // Render every row from current state. Highlights the selected row.
  function render(selectedIndex) {
    panelEl.innerHTML = rows.map((r, i) => {
      const sel = i === selectedIndex ? " selected" : "";
      let priceCell;
      if (r.error) {
        priceCell = '<span class="fetch-drow-err">' + escHtml(r.error) + '</span>';
      } else if (r.unitPrice === null) {
        priceCell = '<span class="fetch-drow-pending">…</span>';
      } else {
        priceCell = '<span class="fetch-drow-unit">' + escHtml(fmt(r.unitPrice)) +
          '</span><span class="fetch-drow-ext">×' + escHtml(String(r.qty)) + ' = ' +
          escHtml("$" + Number(r.extPrice).toFixed(2)) + '</span>';
      }
      const pnCell = r.editing
        ? '<input type="text" class="fetch-drow-edit-input" data-idx="' + i + '" value="' + escHtml(r.partNumber) + '">' +
          '<button class="fetch-drow-edit-confirm-btn" data-idx="' + i + '" title="Save corrected part number">✓</button>' +
          '<button class="fetch-drow-edit-cancel-btn" data-idx="' + i + '" title="Cancel">✕</button>'
        : '<span class="fetch-drow-pn">' + escHtml(r.partNumber) + '</span>' +
          '<button class="fetch-drow-edit-btn" data-idx="' + i + '" title="Correct this part number">✎</button>' +
          '<button class="fetch-drow-delete-btn' + (r.armed ? ' armed' : '') + '" data-idx="' + i + '" title="Clear this distributor’s part number">' +
          (r.armed ? 'Really clear?' : '✕') + '</button>';
      return '<div class="fetch-drow' + sel + '" data-idx="' + i + '">' +
        '<span class="fetch-drow-label">' + escHtml(r.label) + '</span>' +
        '<input type="number" class="fetch-drow-qty" min="1" step="1" value="' +
          escHtml(String(r.qty)) + '" data-idx="' + i + '">' +
        pnCell +
        priceCell + '</div>';
    }).join("");
    panelEl.classList.toggle("hidden", rows.length === 0);
    const editInput = /** @type {HTMLInputElement} */ (panelEl.querySelector(".fetch-drow-edit-input"));
    if (editInput) { editInput.focus(); editInput.select(); }
  }

  // Recompute one row's price from its fetched tiers + current qty.
  function recompute(i) {
    const r = rows[i];
    if (!r.prices) return;
    const { unitPrice, extPrice } = rowPrice(r.prices, r.qty);
    r.unitPrice = unitPrice;
    r.extPrice = extPrice;
  }

  // Persist a distributor's PN via the existing update_part_fields API, then
  // refresh both the global store and this panel from the fresh item.
  async function applyFix(i, newPn) {
    const r = rows[i];
    const distributor = r.distributor;
    const label = r.label;
    const fresh = await api("update_part_fields", pk, { [distributor]: newPn });
    if (!fresh) return;   // api() already toasted the error
    onInventoryUpdated(fresh);
    const freshItem = fresh.find((it) => invPartKey(it) === pk) || null;
    if (onPartUpdated) onPartUpdated(freshItem);
    if (freshItem) await configure(freshItem);

    // configure() just re-fetched get_sourced_distributors and rebuilt `rows`
    // from the fresh backend state — that's the real signal of whether the
    // write actually landed. update_part_fields matches ledger rows by the
    // strict get_part_key(), while get_sourced_distributors surfaces rows via
    // a looser "any PN column matches" scan; those scopes can disagree, in
    // which case the API call "succeeds" but the targeted row is untouched.
    const resultRow = rows.find((x) => x.distributor === distributor);
    const succeeded = newPn
      ? !!resultRow && resultRow.partNumber === newPn
      : !resultRow;
    if (succeeded) {
      showToast(newPn ? "Corrected " + label + " part number" : "Cleared " + label + " part number");
    } else {
      AppLog.error(
        "update_part_fields did not change " + distributor + " for " + pk +
        " — the ledger row may be keyed under a different part number"
      );
      showToast("Couldn't update " + label + " — it may be recorded under a different part key in the purchase ledger");
    }
  }

  function onDeleteClick(i) {
    const r = rows[i];
    if (!r.armed) {
      r.armed = true;
      render(pinnedIndex);
      return;
    }
    applyFix(i, "");
  }

  function onEditClick(i) {
    rows.forEach((r) => { r.editing = false; r.armed = false; });
    rows[i].editing = true;
    render(pinnedIndex);
  }

  function onEditCancel(i) {
    rows[i].editing = false;
    render(pinnedIndex);
  }

  function onEditConfirm(i) {
    const input = /** @type {HTMLInputElement} */ (
      panelEl.querySelector('.fetch-drow-edit-input[data-idx="' + i + '"]')
    );
    const newPn = input ? input.value.trim() : "";
    if (!newPn || newPn === rows[i].partNumber) {
      rows[i].editing = false;
      render(pinnedIndex);
      return;
    }
    applyFix(i, newPn);
  }

  // Auto-pick cheapest (unless a row is pinned) and push its price to unitInput.
  function applySelection() {
    const idx = pinnedIndex >= 0 ? pinnedIndex : cheapestRow(rows);
    if (idx >= 0 && rows[idx].unitPrice !== null) setUnitPrice(rows[idx].unitPrice);
    render(idx);
  }

  // Fetch one distributor row's live price; on failure fall back to cached
  // get_price_summary, else mark the row unavailable.
  async function fetchRow(i, priceSummary) {
    const r = rows[i];
    try {
      // Call the bridge directly (not api()) so a scraper error becomes this row's
      // "unavailable" state instead of a global error toast — we auto-fetch every
      // sourced distributor on open, so api()'s global toast would fire per failure.
      const bridge = /** @type {any} */ (window).pywebview.api;
      const product = await bridge[r.method](r.partNumber);
      if (product && typeof product.description === "string") {
        r.description = product.description;
      }
      if (product && Array.isArray(product.prices) && product.prices.length) {
        r.prices = product.prices;
        recompute(i);
        // fire-and-forget price-history logging (unchanged behavior)
        api("record_fetched_prices", pk, r.distributor, product.prices).catch(() => {});
        return;
      }
    } catch (e) {
      AppLog.warn("Price fetch failed for " + r.distributor + " " + r.partNumber + ": " + (e && e.message));
    }
    // Fallback: last-known cached price for this distributor.
    const cached = priceSummary && priceSummary[r.distributor];
    if (cached && typeof cached.latest_unit_price === "number") {
      r.unitPrice = cached.latest_unit_price;
      r.extPrice = cached.latest_unit_price * r.qty;
    } else {
      r.error = "unavailable";
    }
  }

  // qty edits: update that row, re-select (unless pinned to another row).
  panelEl.addEventListener("input", (e) => {
    const input = /** @type {HTMLElement} */ (e.target);
    if (!input.classList.contains("fetch-drow-qty")) return;
    const i = Number(input.dataset.idx);
    const q = parseInt(/** @type {HTMLInputElement} */ (input).value, 10);
    rows[i].qty = q > 0 ? q : 1;
    recompute(i);
    applySelection();
  });

  // row click (not on qty/edit controls): pin that row.
  panelEl.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    if (target.classList.contains("fetch-drow-qty")) return;

    const delBtn = target.closest(".fetch-drow-delete-btn");
    if (delBtn) { onDeleteClick(Number(/** @type {HTMLElement} */ (delBtn).dataset.idx)); return; }

    const editBtn = target.closest(".fetch-drow-edit-btn");
    if (editBtn) { onEditClick(Number(/** @type {HTMLElement} */ (editBtn).dataset.idx)); return; }

    const editConfirmBtn = target.closest(".fetch-drow-edit-confirm-btn");
    if (editConfirmBtn) { onEditConfirm(Number(/** @type {HTMLElement} */ (editConfirmBtn).dataset.idx)); return; }

    const editCancelBtn = target.closest(".fetch-drow-edit-cancel-btn");
    if (editCancelBtn) { onEditCancel(Number(/** @type {HTMLElement} */ (editCancelBtn).dataset.idx)); return; }

    if (target.classList.contains("fetch-drow-edit-input")) return;

    const rowEl = target.closest(".fetch-drow");
    if (!rowEl) return;
    const i = Number(/** @type {HTMLElement} */ (rowEl).dataset.idx);
    if (rows[i].editing || rows[i].unitPrice === null) return;
    pinnedIndex = i;
    setUnitPrice(rows[i].unitPrice);
    render(i);
  });

  // Enter/Escape while editing a distributor's PN.
  panelEl.addEventListener("keydown", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    if (!target.classList.contains("fetch-drow-edit-input")) return;
    const i = Number(target.dataset.idx);
    if (e.key === "Enter") { e.preventDefault(); onEditConfirm(i); }
    else if (e.key === "Escape") { e.preventDefault(); onEditCancel(i); }
  });

  /** Set up the panel for a newly opened modal. */
  async function configure(part) {
    pk = invPartKey(part);
    pinnedIndex = -1;
    rows = [];
    panelEl.innerHTML = "";
    panelEl.classList.add("hidden");

    const [sourced, lastPoQty, priceSummary, groupNames, hasPurchaseHistory] = await Promise.all([
      api("get_sourced_distributors", pk),
      api("get_last_po_quantity", pk),
      api("get_price_summary", pk).catch(() => ({})),
      api("get_generic_group_names", pk).catch(() => []),
      api("has_purchase_history", pk),
    ]);
    lastGroupNames = groupNames || [];
    lastHasPurchaseHistory = hasPurchaseHistory !== false;
    const defaultQty = (typeof lastPoQty === "number" && lastPoQty > 0)
      ? lastPoQty : (part.qty > 0 ? part.qty : 1);

    rows = (sourced || []).map((s) => {
      const sup = FETCH_SUPPLIERS.find((f) => f.key === s.distributor);
      return {
        distributor: s.distributor,
        label: sup ? sup.label : s.distributor,
        method: sup ? sup.method : "",
        partNumber: s.part_number,
        qty: defaultQty,
        prices: null,
        unitPrice: null,
        extPrice: null,
        error: "",
        armed: false,
        editing: false,
        description: "",
      };
    }).filter((r) => r.method);

    if (rows.length === 0) { render(-1); return; }
    render(-1);  // show pending rows immediately

    await Promise.allSettled(rows.map((_, i) => fetchRow(i, priceSummary)));
    applySelection();
  }

  function deleteEligibility() {
    return { canDelete: !lastHasPurchaseHistory, groupNames: lastGroupNames };
  }

  return {
    configure,
    deleteEligibility,
    hasSourcedRows: () => rows.length > 0,
    bestDescription: () => pickBestDescription(rows, pinnedIndex, cheapestRow(rows)),
  };
}

/**
 * Open the price modal for the given inventory item.
 * The modal is built once (via defineFormModal) and reused.
 *
 * @param {import('./types.js').InventoryItem} item
 */
export function openPriceModal(item) {
  if (!priceFormModal) throw new Error("inventory-modals: init() not called before openPriceModal()");
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
    const fresh = await api("delete_part", pk);
    if (!fresh) return;   // api() already toasted the error
    onInventoryUpdated(fresh);
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

      const fresh = await api("update_part_price", pk, up, ep);
      if (!fresh) return null;

      lastPriceMeta = {
        partKey: pk,
        oldUp: item.unit_price || 0,
        oldEp: item.ext_price  || 0,
        newUp: up,
        newEp: ep,
      };
      onInventoryUpdated(fresh);
      return fresh;
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

// Module-level ref to current price modal ctx (set in the patched open()).
// Used by onInput to access the current item's qty for unit↔ext math.
let _priceCtx = null;
