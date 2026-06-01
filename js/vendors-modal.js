/* vendors-modal.js — Master-detail vendor manager.

   Left: searchable list of all vendors. Right: detail pane to edit name/URL,
   merge a vendor into another (reassigns its POs), delete it, or add a new one.
   Built-in pseudo-vendors (Self/Salvage/Unknown) are shown but not editable —
   the backend rejects mutating them. */

import { apiVendors, AppLog } from './api.js';
import { showToast, escHtml, Modal } from './ui-helpers.js';
import { onInventoryUpdated } from './store.js';

const PSEUDO_TYPES = new Set(["self", "salvage", "unknown"]);

const vendorsModal = Modal("vendors-modal", { cancelId: "vendors-close" });

// ── State ──
let _vendors = [];        // last-loaded vendor list
let _selectedId = null;   // currently-selected vendor id (null = none / add mode)

// ── Helpers ──

function hostOf(url) {
  const s = (url || "").trim();
  if (!s) return "";
  try {
    return new URL(s.includes("://") ? s : "https://" + s).host;
  } catch {
    return s;
  }
}

function iconHtml(v) {
  if (v.favicon_path) {
    return `<img src="${escHtml(v.favicon_path)}" alt="" onerror="this.remove()">`;
  }
  if (v.icon) return escHtml(v.icon);
  const letter = (v.name || "?").trim().charAt(0).toUpperCase() || "?";
  return `<span class="vendor-avatar">${escHtml(letter)}</span>`;
}

// ── List rendering ──

function renderList() {
  const listEl = document.getElementById("vendor-list");
  const term = document.getElementById("vendor-search").value.trim().toLowerCase();
  const matches = _vendors.filter(v => {
    if (!term) return true;
    return v.name.toLowerCase().includes(term)
      || (v.url || "").toLowerCase().includes(term);
  });

  if (!matches.length) {
    listEl.innerHTML = `<div class="vendors-detail-empty">No vendors match.</div>`;
    return;
  }

  listEl.innerHTML = matches.map(v => {
    const sub = hostOf(v.url) || v.type;
    const sel = v.id === _selectedId ? " selected" : "";
    return `<div class="vendor-row${sel}" data-id="${escHtml(v.id)}">
      <span class="vendor-row-icon">${iconHtml(v)}</span>
      <span class="vendor-row-text">
        <span class="vendor-row-name">${escHtml(v.name)}</span>
        <span class="vendor-row-sub">${escHtml(sub)}</span>
      </span>
    </div>`;
  }).join("");
}

// ── Detail rendering ──

function showEmpty() {
  _selectedId = null;
  document.getElementById("vendor-detail").innerHTML =
    `<div class="vendors-detail-empty">Select a vendor to view details</div>`;
}

function renderDetail(v, isNew) {
  const detail = document.getElementById("vendor-detail");
  const isPseudo = !isNew && PSEUDO_TYPES.has(v.type);
  const others = _vendors.filter(x => x.id !== v.id && !PSEUDO_TYPES.has(x.type));

  detail.innerHTML = `
    <div class="vendor-detail-form">
      <label class="vendor-field">
        <span class="vendor-field-label">Name</span>
        <input type="text" class="modal-field-input" id="vendor-name"
               value="${escHtml(v.name || "")}" ${isPseudo ? "disabled" : ""}>
      </label>
      <label class="vendor-field">
        <span class="vendor-field-label">URL</span>
        <input type="text" class="modal-field-input" id="vendor-url"
               value="${escHtml(v.url || "")}" placeholder="https://example.com"
               ${isPseudo ? "disabled" : ""}>
      </label>
      <div class="vendor-field">
        <span class="vendor-field-label">Type</span>
        <span class="vendor-type-badge">${escHtml(isNew ? "new" : v.type)}</span>
      </div>
      ${isPseudo ? `<div class="vendor-pseudo-note">Built-in vendor — cannot be edited, merged, or deleted.</div>` : ""}
      ${isPseudo ? "" : `
      <div class="vendor-detail-actions">
        ${isNew ? "" : `
        <div class="vendor-merge-group">
          <select class="modal-field-input vendor-merge-select" id="vendor-merge-target">
            <option value="">Merge into…</option>
            ${others.map(o => `<option value="${escHtml(o.id)}">${escHtml(o.name)}</option>`).join("")}
          </select>
          <button class="btn-lg btn btn-cancel" id="vendor-merge-btn" disabled>Merge</button>
        </div>`}
        <div class="vendor-action-group">
          ${isNew ? "" : `<button class="btn-lg btn btn-danger" id="vendor-delete-btn">Delete</button>`}
          <button class="btn-lg btn btn-apply" id="vendor-save-btn">Save</button>
        </div>
      </div>`}
    </div>`;

  if (isPseudo) return;

  const nameEl = detail.querySelector("#vendor-name");

  detail.querySelector("#vendor-save-btn").addEventListener("click", () => {
    save(isNew ? "" : v.id, nameEl.value, detail.querySelector("#vendor-url").value);
  });

  if (!isNew) {
    const mergeSel = detail.querySelector("#vendor-merge-target");
    const mergeBtn = detail.querySelector("#vendor-merge-btn");
    mergeSel.addEventListener("change", () => { mergeBtn.disabled = !mergeSel.value; });
    mergeBtn.addEventListener("click", () => {
      if (mergeSel.value) merge(v.id, mergeSel.value);
    });

    // Two-step delete: first click arms, second confirms.
    const delBtn = detail.querySelector("#vendor-delete-btn");
    let armed = false;
    delBtn.addEventListener("click", () => {
      if (!armed) {
        armed = true;
        delBtn.textContent = "Really delete?";
        delBtn.classList.add("armed");
        return;
      }
      remove(v.id);
    });
  }
}

function selectVendor(id) {
  _selectedId = id;
  const v = _vendors.find(x => x.id === id);
  if (!v) { showEmpty(); return; }
  renderList();          // refresh row highlight
  renderDetail(v, false);
}

// ── Backend actions ──

async function refresh(selectId) {
  _vendors = (await apiVendors.list()) || [];
  if (selectId && _vendors.some(v => v.id === selectId)) {
    selectVendor(selectId);
  } else {
    renderList();
    showEmpty();
  }
}

async function save(id, name, url) {
  name = name.trim();
  if (!name) { showToast("Vendor name required"); return; }
  const v = await apiVendors.upsert(id, name, url.trim());
  if (!v) return;        // api() already logged + toasted the error
  showToast(`Saved ${v.name}`);
  AppLog.info(`Vendor saved: ${v.name}`);
  await refresh(v.id);
}

async function remove(id) {
  const fresh = await apiVendors.delete(id);
  if (fresh === undefined) return;   // e.g. "vendor has POs; merge first" — api() toasted it
  onInventoryUpdated(fresh);
  showToast("Vendor deleted");
  await refresh(null);
}

async function merge(srcId, dstId) {
  const fresh = await apiVendors.merge(srcId, dstId);
  if (fresh === undefined) return;
  onInventoryUpdated(fresh);
  showToast("Vendors merged");
  AppLog.info(`Merged vendor ${srcId} → ${dstId}`);
  await refresh(dstId);
}

// ── Open / wire ──

export async function openVendorsModal() {
  await refresh(null);
  vendorsModal.open();
}

export function wireVendorsModal() {
  const btn = document.getElementById("vendors-btn");
  if (btn) btn.addEventListener("click", openVendorsModal);

  const addBtn = document.getElementById("vendor-add-btn");
  if (addBtn) addBtn.addEventListener("click", () => {
    _selectedId = null;
    renderList();
    renderDetail({ id: "", name: "", url: "", type: "real" }, true);
  });

  const search = document.getElementById("vendor-search");
  if (search) search.addEventListener("input", renderList);

  const list = document.getElementById("vendor-list");
  if (list) list.addEventListener("click", (e) => {
    const row = e.target.closest(".vendor-row");
    if (row) selectVendor(row.dataset.id);
  });
}
