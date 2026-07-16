// @ts-check
/* sse.js — Thin client for the server's /v1/events SSE stream (server/routes/events.py).
   Server pushes (scan.receiving/scan.received, inventory.updated, inventory.consumed)
   replace the old evaluate_js pushes; see docs/plans/2026-07-16-phase1b-frontend-port-design.md.

   CRITICAL (spike finding, Task 1 ledger): WebView2's EventSource does NOT
   reliably fire `onopen`. Never key readiness or app logic on `onopen` —
   readiness is message delivery / readyState, not the open event. Native
   auto-reconnect (built into EventSource) is sufficient; do not build a
   custom reconnect loop on top of it. */

/** @type {EventSource|null} */
let _es = null;

/** @type {Map<string, Set<(data: any) => void>>} */
const _handlers = new Map();

/** Event names that already have an addEventListener wired on the current `_es`. */
const _wired = new Set();

function _wire(name) {
  if (!_es || _wired.has(name)) return;
  _wired.add(name);
  _es.addEventListener(name, (e) => {
    const fns = _handlers.get(name);
    if (!fns || fns.size === 0) return;
    const data = JSON.parse(/** @type {MessageEvent} */(e).data);
    fns.forEach((fn) => fn(data));
  });
}

/**
 * Register a handler for a named SSE event. Safe to call before `connectEvents()`
 * — handlers registered early are wired up once the connection opens.
 * @param {string} name
 * @param {(data: any) => void} fn
 */
export function onEvent(name, fn) {
  if (!_handlers.has(name)) _handlers.set(name, new Set());
  _handlers.get(name).add(fn);
  _wire(name);
}

/**
 * Opens the SSE connection (idempotent — a second call is a no-op while one
 * is already open) and wires every handler registered via `onEvent` so far
 * (and any registered afterward).
 * @param {string} [baseUrl]
 * @returns {EventSource}
 */
export function connectEvents(baseUrl = "") {
  if (_es) return _es;
  _es = new EventSource(baseUrl + "/v1/events");
  for (const name of _handlers.keys()) _wire(name);
  return _es;
}
