/* api.js — /v1 HTTP transport + pywebview bridge fallback + application log */

import { escHtml, showToast } from './ui-helpers.js';
import { API_MAP } from './api-map.js';

const LOG_MAX_ENTRIES = 200;

/* Log observers. js/panel-collapse.js registers here so a warning can reopen a
   collapsed log viewer without api.js importing the panel layer — that layer
   imports AppLog, so importing it back would be a cycle. */
const _logObservers = [];

/**
 * Subscribe to log entries. Called with the level ("info" | "warn" | "error").
 * @param {(level: string) => void} fn
 */
export function onLogEntry(fn) { _logObservers.push(fn); }

function notifyLogObservers(level) {
  for (const fn of _logObservers) {
    // An observer must never be able to swallow the log line that triggered it.
    try { fn(level); } catch (e) { console.error("log observer failed", e); }
  }
}

export const AppLog = {
  _entries: [],
  _max: LOG_MAX_ENTRIES,
  _add(level, msg) {
    const entry = { level, msg, time: new Date() };
    this._entries.push(entry);
    if (this._entries.length > this._max) this._entries.shift();
    // Notify before the DOM early-return below: a warning must reopen a
    // collapsed console even when #console-entries is not currently rendered.
    notifyLogObservers(level);
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

/* The header the hub reads to decide which server serves a request
   (server/proxy.py's SOURCE_HEADER, server/dispatch.py's resolve_target).

   Absent means "the hub's persisted default" — which is right for
   tools/dubis-cli and curl, and WRONG for an app window, because several
   windows are open on different servers on purpose and the default is whichever
   one saved it last. server/dispatch.py states the contract: a dubIS window
   sends this on every /v1 request, including the first of a page load. So every
   call carries it, from the provider below; `apiOn` overrides it per call when
   a write has to land on one specific server. */
const SOURCE_HEADER = "X-Dubis-Source";

/* Supplies this window's own source value. Installed by js/store.js at import
   time — a function rather than a value so api.js never imports the store, which
   imports api.js.

   Returns "" until preferences have loaded and the tab signal is populated. The
   only request that goes out in that window is `load_preferences`, whose route
   is local-only and is never proxied, so the brief header-less period cannot
   read another window's server. */
let _sourceHeaderProvider = null;

/**
 * Install the provider of this window's `X-Dubis-Source` value.
 * @param {() => string} fn returns "" when this window does not know yet
 */
export function setSourceHeaderProvider(fn) { _sourceHeaderProvider = fn; }

function windowSource() {
  if (!_sourceHeaderProvider) return "";
  try {
    return _sourceHeaderProvider() || "";
  } catch (e) {
    // A broken provider must not take the whole API surface down with it. A
    // header-less request is served from the default: degraded, not wrong, and
    // logged rather than silent.
    AppLog.warn("api: source header provider failed: " + e.message);
    return "";
  }
}

/* The hub answers 400 to a write that names `merged` (server/sources.py's
   Registry.resolve): a merged view has no single owner to write to. Caught
   here as well so the mistake is named in the app log rather than surfacing as
   a bare HTTP 400 from a route the user never asked about. */
const MERGED_SOURCE = "merged";

/**
 * @param {Object} entry an API_MAP entry
 * @param {Array<any>} args positional args, mapped onto entry.argOrder
 * @param {{sourceId?: string, raw?: boolean}} [opts]
 *   `sourceId` sets the X-Dubis-Source header; `raw` returns the whole response
 *   body instead of the `entry.unwrap` key.
 */
async function callHttp(entry, args, opts = {}) {
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
  // An explicit per-call source (a routed write) outranks the window's own; when
  // there is neither, the request goes out bare and the hub falls back to its
  // persisted default.
  const source = opts.sourceId || windowSource();
  if (source) {
    init.headers = Object.assign({}, init.headers, { [SOURCE_HEADER]: source });
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
  if (opts.raw) return data;
  if (entry.unwrap) return data[entry.unwrap];
  return data;
}

/**
 * Whether `api(method, ...)` has any transport for `method` at all.
 *
 * Mirrors the dispatch in `api()` below, deliberately — HTTP when the method is
 * in the generated map, the client shell otherwise. Callers use it to decide
 * whether a *capability* exists, not whether a call will succeed: a route that
 * is mapped can still 500. It exists because `api()` reports a missing method
 * the same way it reports a real failure (a logged error plus a toast), which
 * is right for a call the user asked for and wrong for a feature probing
 * whether it should render itself at all.
 * @param {string} method
 * @returns {boolean}
 */
export function apiSupports(method) {
  if (API_MAP[method]) return true;
  const bridge = window.pywebview && window.pywebview.api;
  return !!(bridge && typeof bridge[method] === "function");
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

/**
 * `api(method, ...)`, but pinned to ONE dubIS server.
 *
 * The seam for writes in a merged view. A merged row's stock is the sum of
 * several servers' stock, and an adjustment has to land on the server that
 * actually holds it — so the caller names that server and this sets
 * `X-Dubis-Source`, which the hub honours per request (server/dispatch.py).
 *
 * A header rather than an extra argument, and a separate function rather than a
 * flag on `api()`, for one concrete reason: `api()` maps its args POSITIONALLY
 * onto `entry.argOrder` across ~141 call sites, so a new positional parameter
 * would silently become the first body field of every one of them. This touches
 * only the handful of mutation sites that route.
 *
 * `sourceId` of `""`/`null`/`"local"`-when-not-merged is not an error: it means
 * "no routing needed", and the call behaves exactly like `api()` — which is what
 * lets a call site use this unconditionally instead of branching on whether the
 * view happens to be merged.
 *
 * @param {string|null|undefined} sourceId roster source id, or falsy for "don't route"
 * @param {string} method
 * @param {...any} args
 */
export async function apiOn(sourceId, method, ...args) {
  const id = typeof sourceId === "string" ? sourceId.trim() : "";
  if (!id) return api(method, ...args);
  try {
    if (id === MERGED_SOURCE) {
      // Loud rather than silent: guessing a server here is exactly the bug the
      // whole routing seam exists to prevent.
      throw new Error(
        "cannot route a write to the merged view — it has no single owning server",
      );
    }
    const entry = API_MAP[method];
    if (!entry) {
      throw new Error(
        "cannot route " + method + " to source " + id +
        ": it has no /v1 route, so there is no request to put a source header on",
      );
    }
    return await callHttp(entry, args, { sourceId: id });
  } catch (e) {
    AppLog.error(method + " -> " + id + ": " + e.message);
    showToast("Error: " + e.message);
    return undefined;
  }
}

/**
 * `api(method, ...)` without the `entry.unwrap` step — the whole response body.
 *
 * Exists for exactly one thing today: `GET /v1/parts` answers a merged view with
 * `{inventory, sources}`, where `sources` carries the per-source ok/error status.
 * `api()` unwraps to `inventory` and drops the sibling, which would leave a view
 * that is silently missing a whole server's stock looking indistinguishable from
 * a complete one — the worst outcome this feature has.
 *
 * @param {string} method
 * @param {...any} args
 * @returns {Promise<any>} the response body, or undefined on failure
 */
export async function apiEnvelope(method, ...args) {
  try {
    const entry = API_MAP[method];
    if (!entry) {
      // The bridge has no envelope to speak of — it returns whatever the
      // client-shell method returns, which is already "the whole thing".
      return await window.pywebview.api[method](...args);
    }
    return await callHttp(entry, args, { raw: true });
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
