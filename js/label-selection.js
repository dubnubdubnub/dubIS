// @ts-check
/* label-selection.js — Owns "Labels" mode state: the mode flag, the selected
   part-key Set, the current tape width, and the preview handler.

   The row checkboxes / renderer integration live in a separate task. This
   module is the single source of truth that the toolbar, PO picker, and (later)
   the row renderers all talk to. */

import { EventBus, Events } from './event-bus.js';
import { store } from './store.js';
import { invPartKey } from './part-keys.js';
import { showToast, escHtml } from './ui-helpers.js';
import { api, AppLog } from './api.js';

// ── Private state ─────────────────────────────────────────
let labelMode = false;
/** @type {Set<string>} */
const selected = new Set();
/** @type {"6mm"|"12mm"} */
let tape = "6mm";

/** @type {(items: object[], tape: "6mm"|"12mm") => void} */
let previewHandler = (items, tapeWidth) => {
  // Default no-op handler — the real preview modal registers itself in a later
  // task via setPreviewHandler(). Keeps the tasks decoupled.
  AppLog.warn(`Label preview not available (${items.length} item(s), ${tapeWidth})`);
  showToast("Label preview not available");
};

// ── Mode ──────────────────────────────────────────────────
export function isLabelMode() {
  return labelMode;
}

export function enterLabelMode() {
  if (labelMode) return;
  labelMode = true;
  EventBus.emit(Events.LABEL_MODE, labelMode);
}

export function exitLabelMode() {
  if (!labelMode) return;
  labelMode = false;
  clearSelection();
  EventBus.emit(Events.LABEL_MODE, labelMode);
}

// ── Selection ─────────────────────────────────────────────
function notifySelectionChanged() {
  EventBus.emit(Events.LABEL_SELECTION_CHANGED, selected.size);
}

export function select(key) {
  if (!key) return;
  if (selected.has(key)) return;
  selected.add(key);
  notifySelectionChanged();
}

export function deselect(key) {
  if (!key) return;
  if (!selected.has(key)) return;
  selected.delete(key);
  notifySelectionChanged();
}

export function toggleSelection(key) {
  if (!key) return;
  if (selected.has(key)) selected.delete(key);
  else selected.add(key);
  notifySelectionChanged();
}

export function isSelected(key) {
  return selected.has(key);
}

export function clearSelection() {
  if (selected.size === 0) return;
  selected.clear();
  notifySelectionChanged();
}

export function selectedCount() {
  return selected.size;
}

/** Add every loaded inventory item whose po_history includes `poId`. */
export function selectPo(poId) {
  if (!poId) return 0;
  let added = 0;
  for (const item of store.inventory) {
    const hist = item.po_history || [];
    if (hist.includes(poId)) {
      const key = invPartKey(item);
      if (key && !selected.has(key)) {
        selected.add(key);
        added++;
      }
    }
  }
  if (added > 0) {
    notifySelectionChanged();
    // Bulk select can flip many keys at once; checkboxes already in the DOM
    // won't reflect the new state until the next re-render. Signal listeners
    // (the inventory panel) to re-render so visible checkboxes update. Single
    // toggles deliberately do NOT emit this — re-rendering on every checkbox
    // click would hurt scroll/perf.
    EventBus.emit(Events.LABEL_BULK_SELECTION, added);
  }
  return added;
}

// ── Resolve selected keys → full item objects ─────────────
/**
 * Resolve the selected part keys back to full item objects, preferring
 * inventory items. If a selected key matches only a BOM row's matched
 * inventory item, that is used too. Uses the SAME key helper the renderers use.
 * @returns {object[]}
 */
export function getSelectedItems() {
  /** @type {Map<string, object>} */
  const byKey = new Map();

  for (const item of store.inventory) {
    const key = invPartKey(item);
    if (key && selected.has(key) && !byKey.has(key)) {
      byKey.set(key, item);
    }
  }

  // BOM rows: each result has a matched inventory item under `.inv`.
  const bomResults = store.bomResults || [];
  for (const row of bomResults) {
    const inv = row && row.inv;
    if (!inv) continue;
    const key = invPartKey(inv);
    if (key && selected.has(key) && !byKey.has(key)) {
      byKey.set(key, inv);
    }
  }

  return Array.from(byKey.values());
}

// ── Tape width ────────────────────────────────────────────
export function getTape() {
  return tape;
}

export function setTape(width) {
  if (width !== "6mm" && width !== "12mm") {
    throw new Error(`Invalid tape width: ${width}`);
  }
  tape = width;
}

// ── Preview handler ───────────────────────────────────────
export function setPreviewHandler(fn) {
  if (typeof fn !== "function") {
    throw new Error("setPreviewHandler expects a function");
  }
  previewHandler = fn;
}

/** Invoke the registered preview handler with the current selection + tape. */
export function createLabels() {
  const items = getSelectedItems();
  if (items.length === 0) {
    showToast("No parts selected");
    AppLog.warn("Create Labels: no parts selected");
    return;
  }
  previewHandler(items, tape);
}

// ── UI wiring (toolbar + PO picker) ───────────────────────
// State lives above; this section only touches the DOM. Kept here so the
// "Labels" feature is self-contained and app-init just calls init().

let poListLoaded = false;

function fmtPoLabel(po) {
  const id = po.po_id || po.id || "";
  const date = po.purchase_date || po.date || "";
  const vendor = po.vendor_id || "";
  const parts = [id, date, vendor].filter(Boolean);
  return parts.join(" · ");
}

async function renderPoList(listEl) {
  if (poListLoaded) return;
  listEl.innerHTML = "";
  /** @type {any[]} */
  let pos;
  try {
    pos = await api("list_purchase_orders");
  } catch (err) {
    AppLog.error("renderPoList: failed to load purchase orders: " + (err && err.message || err));
    listEl.innerHTML = '<div class="label-po-empty">Failed to load purchase orders</div>';
    return;
  }
  poListLoaded = true;
  if (!Array.isArray(pos) || pos.length === 0) {
    listEl.innerHTML = '<div class="label-po-empty">No purchase orders</div>';
    return;
  }
  for (const po of pos) {
    const poId = po.po_id || po.id || "";
    const row = document.createElement("div");
    row.className = "label-po-row";

    const head = document.createElement("div");
    head.className = "label-po-head";
    head.innerHTML =
      '<button class="label-po-expand" type="button" title="Show line items">▸</button>' +
      '<button class="label-po-select" type="button">Select PO</button>' +
      '<span class="label-po-label">' + escHtml(fmtPoLabel(po)) + '</span>';
    row.appendChild(head);

    const detail = document.createElement("div");
    detail.className = "label-po-detail hidden";
    row.appendChild(detail);

    let loadedItems = false;
    const expandBtn = head.querySelector(".label-po-expand");
    expandBtn.addEventListener("click", async () => {
      const willShow = detail.classList.contains("hidden");
      detail.classList.toggle("hidden");
      expandBtn.textContent = willShow ? "▾" : "▸";
      if (willShow && !loadedItems) {
        loadedItems = true;
        detail.innerHTML = '<div class="label-po-loading">Loading…</div>';
        /** @type {any} */
        let data;
        try {
          data = await api("get_po_with_items", poId);
        } catch (err) {
          AppLog.error("renderPoList: failed to load items for PO " + poId + ": " + (err && err.message || err));
          detail.innerHTML = '<div class="label-po-empty">Failed to load items</div>';
          return;
        }
        const items = (data && data.line_items) || [];
        if (items.length === 0) {
          detail.innerHTML = '<div class="label-po-empty">No line items</div>';
          return;
        }
        const rows = items.map(li =>
          '<tr><td>' + escHtml(li.mpn || "") + '</td>' +
          '<td>' + escHtml(li.manufacturer || "") + '</td>' +
          '<td>' + escHtml(li.package || "") + '</td>' +
          '<td class="num">' + escHtml(String(li.quantity ?? "")) + '</td></tr>'
        ).join("");
        detail.innerHTML =
          '<table class="label-po-items"><thead><tr>' +
          '<th>MPN</th><th>Mfr</th><th>Pkg</th><th class="num">Qty</th>' +
          '</tr></thead><tbody>' + rows + '</tbody></table>';
      }
    });

    head.querySelector(".label-po-select").addEventListener("click", () => {
      const n = selectPo(poId);
      showToast(n + " part" + (n === 1 ? "" : "s") + " added from " + poId);
    });

    listEl.appendChild(row);
  }
}

export function init() {
  const modeBtn = document.getElementById("label-mode-btn");
  const toolbar = document.getElementById("label-toolbar");
  const countEl = document.getElementById("label-selected-count");
  const createBtn = document.getElementById("label-create-btn");
  const doneBtn = document.getElementById("label-done-btn");
  const tapeToggle = document.getElementById("label-tape-toggle");
  const poList = document.getElementById("label-po-list");

  if (!modeBtn || !toolbar) return; // not on this page

  function syncToolbar() {
    const on = isLabelMode();
    toolbar.classList.toggle("hidden", !on);
    modeBtn.classList.toggle("active", on);
    if (on) renderPoList(poList);
  }

  function syncCount() {
    if (countEl) countEl.textContent = selectedCount() + " selected";
  }

  modeBtn.addEventListener("click", () => {
    if (isLabelMode()) exitLabelMode();
    else enterLabelMode();
  });

  if (doneBtn) doneBtn.addEventListener("click", () => exitLabelMode());
  if (createBtn) createBtn.addEventListener("click", () => createLabels());

  if (tapeToggle) {
    tapeToggle.addEventListener("change", (e) => {
      const t = /** @type {HTMLInputElement} */ (e.target);
      if (t && t.name === "label-tape") setTape(t.value);
    });
  }

  EventBus.on(Events.LABEL_MODE, syncToolbar);
  EventBus.on(Events.LABEL_SELECTION_CHANGED, syncCount);

  syncToolbar();
  syncCount();
}
