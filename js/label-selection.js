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
import { api, AppLog, whenPywebviewReady } from './api.js';
import { openPoImageLightbox } from './po-image-lightbox.js';

// Source extensions the backend can return as an inline image (images render
// directly; PDFs are rasterized first-page). Spreadsheet/CSV sources have no
// image preview, so we skip the fetch entirely for them.
const RENDERABLE_SOURCE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".pdf"]);

/** True if a PO has an archived source file we can show as a thumbnail. */
function poHasRenderableSource(po) {
  const ext = String(po.source_file_ext || "").toLowerCase();
  return !!po.source_file_hash && RENDERABLE_SOURCE_EXTS.has(ext);
}

/**
 * Fetch a PO's source image and render a clickable thumbnail into `thumbEl`.
 * Clicking blows it up in the shared lightbox. No-op (leaves thumb empty) when
 * the backend reports no renderable preview.
 */
async function loadPoThumbnail(thumbEl, poId) {
  /** @type {any} */
  let preview;
  try {
    preview = await api("get_po_source_preview", poId);
  } catch (err) {
    AppLog.warn("loadPoThumbnail: preview failed for " + poId + ": " + (err && err.message || err));
    return;
  }
  if (!preview || preview.kind !== "image" || !preview.data_uri) return;
  const img = document.createElement("img");
  img.className = "label-po-thumb-img";
  img.src = preview.data_uri;
  img.alt = "PO source";
  img.title = "Click to enlarge";
  img.addEventListener("click", () => openPoImageLightbox(preview.data_uri, "Source for " + poId));
  thumbEl.appendChild(img);
}

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
  for (const item of /** @type {import('./types.js').InventoryItem[]} */ (store.inventory)) {
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
  // Show most recent POs at the top. Dates are ISO (YYYY-MM-DD) so they sort
  // lexically; blanks fall to the bottom. The backend returns POs in CSV
  // append order (oldest first), so reverse() first makes the newest-added PO
  // win ties on equal/blank dates (sort is stable since ES2019).
  pos = pos.slice().reverse().sort((a, b) => {
    const da = a.purchase_date || a.date || "";
    const db = b.purchase_date || b.date || "";
    if (da === db) return 0;
    if (!da) return 1;
    if (!db) return -1;
    return da < db ? 1 : -1;
  });
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
        // Body = line items on the left, source thumbnail floated to the right
        // edge. The thumbnail loads independently so a slow/absent image never
        // blocks the items from rendering.
        const itemsHtml = items.length === 0
          ? '<div class="label-po-empty">No line items</div>'
          : '<table class="label-po-items"><thead><tr>' +
            '<th>MPN</th><th>Mfr</th><th>Pkg</th><th class="num">Qty</th>' +
            '</tr></thead><tbody>' + items.map(li =>
              '<tr><td>' + escHtml(li.mpn || "") + '</td>' +
              '<td>' + escHtml(li.manufacturer || "") + '</td>' +
              '<td>' + escHtml(li.package || "") + '</td>' +
              '<td class="num">' + escHtml(String(li.quantity ?? "")) + '</td></tr>'
            ).join("") + '</tbody></table>';
        detail.innerHTML =
          '<div class="label-po-detail-body">' +
          '<div class="label-po-detail-items">' + itemsHtml + '</div>' +
          '<div class="label-po-thumb"></div>' +
          '</div>';
        if (poHasRenderableSource(po)) {
          const thumbEl = detail.querySelector(".label-po-thumb");
          if (thumbEl) loadPoThumbnail(thumbEl, poId);
        }
      }
    });

    head.querySelector(".label-po-select").addEventListener("click", () => {
      // Selecting a PO from the (possibly dimmed) history auto-activates Print
      // Labels mode so the selection is visible and actionable — the picker
      // pops out and the row checkboxes appear.
      if (!isLabelMode()) enterLabelMode();
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
  const poPicker = document.getElementById("label-po-picker");

  if (!modeBtn || !toolbar) return; // not on this page

  function syncToolbar() {
    const on = isLabelMode();
    toolbar.classList.toggle("hidden", !on);
    modeBtn.classList.toggle("active", on);
    // Pressing the button again exits the mode, so label it "Cancel" while on.
    modeBtn.textContent = on ? "Cancel" : "Print Labels";
    modeBtn.title = on ? "Exit label mode" : "Select parts and export Epson labels";
    // The PO picker lives in the import panel and is always visible (dimmed) as
    // a PO history; it "pops out" while Print Labels mode is active.
    if (poPicker) poPicker.classList.toggle("is-label-active", on);
  }

  // Re-pull the PO list after an import so newly added POs appear in the
  // history. (renderPoList no-ops while already loaded, so reset the guard.)
  function refreshPoList() {
    poListLoaded = false;
    if (poList) renderPoList(poList);
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
  EventBus.on(Events.INVENTORY_UPDATED, refreshPoList);

  syncToolbar();
  syncCount();

  // Populate the PO history once the pywebview bridge is hydrated. api() would
  // otherwise swallow the pre-ready error and cache an empty list.
  if (poList) whenPywebviewReady().then(() => renderPoList(poList));
}
