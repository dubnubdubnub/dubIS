// @ts-check
/* fetch-controller.js — the multi-distributor "current price" panel
   controller shared by the Adjust and Price modals, extracted from
   inv-modals.js (Task 2 of refactor-sweep-2). */

import { api, AppLog } from '../api.js';
import { showToast, escHtml, formatMoney } from '../ui-helpers.js';
import { store, scheduleInventoryRefresh } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { pickBestDescription } from './pick-description.js';
import { rowPrice, cheapestRow } from './pricing-utils.js';
import { fetchDistributorProduct, FETCH_SUPPLIERS } from './distributor-fetch.js';

/**
 * Wire the multi-distributor "current price" panel shared by the Adjust and
 * Price modals. Renders one row per distributor the part was sourced from
 * (union of record PNs + purchase-ledger PNs, from get_sourced_distributors),
 * auto-fetches every row's price concurrently on open, and feeds the cheapest
 * row's unit price into `unitInput` (overridable by clicking a row).
 *
 * @param {{panelEl: HTMLElement, unitInput: HTMLInputElement, onPartUpdated?: (freshItem: import('../types.js').InventoryItem|null) => void}} els
 */
export function createFetchController({ panelEl, unitInput, onPartUpdated }) {
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
          escHtml(formatMoney(Number(r.extPrice))) + '</span>';
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
    const result = await api("update_part_fields", pk, { [distributor]: newPn });
    if (!result) return;   // api() already toasted the error
    await scheduleInventoryRefresh();
    const freshItem = store.inventory.find((it) => invPartKey(it) === pk) || null;
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
      // Deliberately bypasses api() (not a call to api("fetch_..._product", …))
      // so a scraper error becomes this row's "unavailable" state instead of
      // a global error toast — every sourced distributor auto-fetches
      // concurrently on open, so api()'s global per-call toast would fire
      // once per row and drown out the real signal. See fetchDistributorProduct.
      const product = await fetchDistributorProduct(r.method, r.partNumber);
      if (product && typeof product.description === "string") {
        r.description = product.description;
      }
      if (product && Array.isArray(product.prices) && product.prices.length) {
        r.prices = product.prices;
        recompute(i);
        // fire-and-forget price-history logging. The packaging metadata rides
        // along so each stored ladder says which packaging it belongs to.
        api("record_fetched_prices", pk, r.distributor, product.prices,
            product.packagings, product.reelQty, product.reelFee).catch(() => {});
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
