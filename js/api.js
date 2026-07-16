/* api.js — /v1 HTTP transport + pywebview bridge fallback + application log */

import { escHtml, showToast } from './ui-helpers.js';
import { API_MAP } from './api-map.js';

const LOG_MAX_ENTRIES = 200;

// Step-1 mutation convention (see docs/plans/2026-07-16-phase1b-frontend-port-design.md,
// Architecture decision 2): every mutating call asks the server to echo the
// fresh inventory back in the same response, so call sites that do
// `const fresh = await api(...); onInventoryUpdated(fresh);` keep working
// unchanged. Flipped to SSE-driven refresh later in the phase.
export const INCLUDE_INVENTORY = true;

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

// Memoized HTTP-availability probe. Must be awaited INSIDE api(), never at
// module top level — a top-level `await fetch(...)` crashes vitest
// collection (see js/constants.js's trap, documented in CLAUDE.md). Computed
// once per module load; under Playwright's serve-static.mjs (no /v1 backend)
// or plain file://+bridge, this resolves false and every mapped call falls
// through to the legacy bridge, so existing mocked-bridge specs keep passing
// until the E2E mocks migrate to HTTP route fixtures.
let _httpProbe = null;
function probeHttp() {
  if (_httpProbe === null) {
    _httpProbe = (async () => {
      try {
        const res = await fetch('/v1/health', { method: 'GET' });
        if (!res || !res.ok) return false;
        // Status alone is not proof: static test servers (serve-static.mjs)
        // answer unknown paths with 200 + index.html. Require the real
        // /v1/health JSON contract before routing traffic over HTTP.
        const body = await res.json();
        return body && body.ok === true;
      } catch {
        return false;
      }
    })();
  }
  return _httpProbe;
}

// Exported so app-init.js can gate `connectEvents()` on the same probe that
// `api()` uses to decide HTTP vs. bridge transport — SSE is only meaningful
// once the /v1 server is actually reachable (see Task 3 of the phase-1b plan).
export async function httpAvailable() {
  if (typeof window !== "undefined" && window.__DUBIS_HTTP__ === false) return false;
  return probeHttp();
}

function buildUrl(entry, argMap) {
  let path = entry.path;
  for (const name of entry.pathParams) {
    path = path.replace("{" + name + "}", encodeURIComponent(argMap[name]));
  }
  const query = new URLSearchParams();
  for (const name of entry.queryParams) {
    if (argMap[name] !== undefined) query.set(name, argMap[name]);
  }
  if (entry.mutating && INCLUDE_INVENTORY) query.set("include", "inventory");
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
    if (entry && await httpAvailable()) {
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
