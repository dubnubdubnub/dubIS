/* api.js — /v1 HTTP transport + pywebview bridge fallback + application log */

import { escHtml, showToast } from './ui-helpers.js';
import { API_MAP } from './api-map.js';

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

function buildUrl(entry, argMap) {
  let path = entry.path;
  for (const name of entry.pathParams) {
    path = path.replace("{" + name + "}", encodeURIComponent(argMap[name]));
  }
  const query = new URLSearchParams();
  for (const name of entry.queryParams) {
    if (argMap[name] !== undefined) query.set(name, argMap[name]);
  }
  const qs = query.toString();
  return qs ? `${path}?${qs}` : path;
}

function buildBody(entry, argMap) {
  if (entry.rawBody) {
    const value = argMap[entry.argOrder[0]];
    return typeof value === "string" ? value : JSON.stringify(value);
  }
  if (!entry.bodyParams.length) return undefined;
  const body = {};
  for (const name of entry.bodyParams) body[name] = argMap[name];
  return JSON.stringify(body);
}

async function callHttp(entry, args) {
  const argMap = {};
  entry.argOrder.forEach((name, i) => { argMap[name] = args[i]; });

  const url = buildUrl(entry, argMap);
  const bodyStr = (entry.verb === "GET" || entry.verb === "DELETE")
    ? undefined
    : buildBody(entry, argMap);

  const init = { method: entry.verb };
  if (bodyStr !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = bodyStr;
  }

  const res = await fetch(url, init);
  if (!res.ok) {
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const errBody = await res.json();
      if (errBody && errBody.error) message = errBody.error;
    } catch {
      // Non-JSON error body — fall back to statusText/status.
    }
    throw new Error(message);
  }

  const data = await res.json();
  if (entry.unwrap) return data[entry.unwrap];
  return data;
}

export async function api(method, ...args) {
  try {
    const entry = API_MAP[method];
    if (entry) {
      return await callHttp(entry, args);
    }
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
// Since Phase 1b Task 8, the bridge is the ~9-method ClientShell (client_shell.py) —
// `set_bom_dirty` is one of its methods and is as stable a sentinel as the old
// `load_preferences` (which moved to the /v1 HTTP surface and is no longer on the
// bridge at all). This probe is bridge-readiness only; HTTP readiness is separate
// (the /v1 server is up before the page is ever served, by construction).
export function whenPywebviewReady() {
  if (typeof window.pywebview?.api?.set_bom_dirty === "function") {
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
    api('create_purchase_order_with_items', vendorId, fileB64, fileName, date, notes, items),
  update: (poId, vendorId, date, notes) =>
    api('update_purchase_order', poId, vendorId, date, notes),
  delete: (poId) => api('delete_purchase_order', poId),
  deleteLast: () => api('delete_last_purchase_order'),
  openSource: (poId) => api('open_source_file', poId),
};

export const apiMfgDirect = {
  parseFile: (path) => api('parse_source_file', path),
  parseFileB64: (b64, name, template = 'generic') =>
    api('parse_source_file_b64', b64, name, template),
  ocrOverlayB64: (b64, name, template = 'generic') =>
    api('ocr_overlay_b64', b64, name, template),
  matchPart: (mpn, mfg) => api('match_part', mpn, mfg),
  startScanSession: (template) => api('start_scan_session', template),
  ocrEngineAvailable: () => api('ocr_engine_available'),
  installTesseract: () => api('install_tesseract'),
};

export const apiWarnings = { get: () => api('get_warnings') };

export const apiFeeders = {
  list:     async () => (await api('list_feeders'))?.feeders || [],
  get:      (tagId) => api('get_feeder', tagId),
  register: (tagId, feederType) => api('register_feeder', tagId, feederType),
  load:     (tagId, partKey, qty, tapeWidthMm) =>
    api('load_feeder_reel', tagId, partKey, qty, tapeWidthMm),
  unload:   (tagId) => api('unload_feeder', tagId),
};
