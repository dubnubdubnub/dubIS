/* api.js — pywebview bridge + application log */

import { escHtml, showToast } from './ui-helpers.js';

const LOG_MAX_ENTRIES = 200;

export const AppLog = {
  _entries: [],
  _max: LOG_MAX_ENTRIES,
  _add(level, msg) {
    const entry = { level, msg, time: new Date() };
    this._entries.push(entry);
    if (this._entries.length > this._max) this._entries.shift();
    const el = document.getElementById("console-entries");
    if (!el) return;
    const div = document.createElement("div");
    div.className = "console-entry console-" + level;
    const t = entry.time.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
    div.innerHTML = `<span class="console-time">${t}</span>${escHtml(msg)}`;
    el.appendChild(div);
    while (el.children.length > this._max) el.removeChild(el.firstChild);
    el.scrollTop = el.scrollHeight;
  },
  info(msg)  { this._add("info", msg); },
  warn(msg)  { this._add("warn", msg); },
  error(msg) { this._add("error", msg); },
  clear() {
    this._entries = [];
    const el = document.getElementById("console-entries");
    if (el) el.innerHTML = "";
  }
};

export async function api(method, ...args) {
  try {
    return await window.pywebview.api[method](...args);
  } catch (e) {
    AppLog.error(method + ": " + e.message);
    showToast("Error: " + e.message);
    return undefined;
  }
}

// pywebview hydrates the JS bridge in two phases: api.js creates `window.pywebview = { api: {} }`
// (a truthy empty placeholder), then finish.js calls _createApi(funcList) and dispatches
// `pywebviewready`. Code that calls API methods before phase 2 hits "is not a function".
// Probe for a known stable method to distinguish the placeholder from a hydrated bridge.
export function whenPywebviewReady() {
  if (typeof window.pywebview?.api?.load_preferences === "function") {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
}

export const apiVendors = {
  list:    () => api('list_vendors'),
  upsert:  (id, name, url) => api('update_vendor', id, name, url),
  merge:   (srcId, dstId) => api('merge_vendors', srcId, dstId),
  delete:  (id) => api('delete_vendor', id),
  fetchFavicon: (url) => api('fetch_favicon', url),
};

export const apiPurchaseOrders = {
  list:   () => api('list_purchase_orders'),
  create: (vendorId, fileB64, fileName, date, notes, items) =>
    api('create_purchase_order_with_items', vendorId, fileB64, fileName, date, notes, JSON.stringify(items)),
  update: (poId, vendorId, date, notes) =>
    api('update_purchase_order', poId, vendorId, date, notes),
  delete: (poId) => api('delete_purchase_order', poId),
  openSource: (poId) => api('open_source_file', poId),
};

export const apiMfgDirect = {
  parseFile: (path) => api('parse_source_file', path),
  matchPart: (mpn, mfg) => api('match_part', mpn, mfg),
};
