/* ui-helpers.js — DOM utility functions shared across panels */

import { trap, release } from './a11y/focus-trap.js';

// Unified on the attribute-safe escapeHtml (js/dom/html.js) — the old
// textContent-based implementation here did not escape " or ', which was
// unsafe when interpolated into HTML attribute values. Kept as a named
// re-export so the ~29 existing importers of escHtml are unaffected.
export { escapeHtml as escHtml } from './dom/html.js';

let _enterSubmitEnabled = () => true;
export function setEnterSubmitEnabled(fn) { _enterSubmitEnabled = fn; }

const TOAST_DURATION_MS = 2500;

const STOCK_COLOR_STOPS = [
  { r: 248, g: 81, b: 73 },   // #f85149  red
  { r: 240, g: 136, b: 62 },  // #f0883e  orange
  { r: 210, g: 153, b: 34 },  // #d29922  yellow
  { r: 63, g: 185, b: 80 },   // #3fb950  green
];

/**
 * Format a number as a money string ("$" + 2 decimals). Non-finite values
 * (null, undefined, NaN) render as `fallback` instead.
 * @param {number|null|undefined} n
 * @param {{ fallback?: string }} [options]
 * @returns {string}
 */
export function formatMoney(n, { fallback = '—' } = {}) {
  if (typeof n !== 'number' || !isFinite(n)) return fallback;
  return '$' + n.toFixed(2);
}

export function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), TOAST_DURATION_MS);
}

/**
 * Resolve a vendor's stored favicon_path into a browser-usable img src.
 *
 * favicon_path is stored relative to the data/ dir using OS path separators
 * (e.g. "sources\\favicons\\abc.png" on Windows, or "lcsc-icon.ico"). The page
 * is served from the repo root, so paths need forward slashes and a "data/"
 * prefix. URLs, data/blob URIs, and absolute paths are passed through untouched.
 * @param {string} path
 * @returns {string}
 */
export function vendorIconSrc(path) {
  if (!path) return "";
  const p = String(path).replace(/\\/g, "/");
  if (/^(https?:|data:|blob:|file:)/i.test(p)) return p;
  if (/^[a-zA-Z]:\//.test(p) || p.startsWith("/")) return p;
  if (p.startsWith("data/")) return p;
  return "data/" + p.replace(/^\/+/, "");
}

/**
 * Resolve a whole vendor record into an img src, preferring the backend's
 * inlined `data:` URI over the filesystem path.
 *
 * The static `data/...` URL that vendorIconSrc builds only resolves when the
 * page's static root and the data dir are the same directory — true for the
 * desktop app (both are the repo), false for dubis-server, which serves /app
 * and keeps user data in /data. Favicons fetched for a vendor land in the data
 * dir (`sources/favicons/<hash>.png`), so on a remote client that URL is always
 * a 404; `favicon_data_uri`, which /v1/vendors already returns for every vendor
 * with a cached favicon, carries the bytes and works in both. The path stays as
 * a fallback for the icons shipped in the image (data/lcsc-icon.ico and
 * friends), which are reachable as static assets in both deployments.
 * @param {{ favicon_data_uri?: string, favicon_path?: string }} vendor
 * @returns {string}
 */
export function vendorIconFor(vendor) {
  if (!vendor) return "";
  return vendor.favicon_data_uri || vendorIconSrc(vendor.favicon_path || "");
}

export function Modal(id, { onClose, cancelId, confirmId } = {}) {
  const el = document.getElementById(id);
  // Deferred trap: if a modal is opened and closed within the same frame, the trap
  // may fire after close on a stale element; the next open clears it via release() inside trap().
  function open()  { el.classList.remove("hidden"); requestAnimationFrame(() => trap(el)); }
  function close() { el.classList.add("hidden"); release(); if (onClose) onClose(); }
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  if (cancelId) document.getElementById(cancelId).addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (el.classList.contains("hidden")) return;
    if (e.key === "Escape") { close(); return; }
    if (e.key === "Enter" && confirmId && _enterSubmitEnabled()) {
      const t = e.target;
      // Don't hijack Enter from controls with their own Enter semantics
      // (buttons/selects/links fire natively; textarea + #adj-note want newlines).
      if (t instanceof Element && t.closest('textarea, select, button, a[href], #adj-note')) return;
      e.preventDefault();
      const btn = document.getElementById(confirmId);
      if (btn && !btn.disabled) btn.click();
    }
  });
  return { el, open, close };
}

export function setupDropZone(zoneId, inputId, onBrowse, onFile, { multi = false } = {}) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  // Only treat clicks on the zone's empty space as a "browse" gesture. Clicks on
  // interactive controls inside the zone (the file input, the OCR template
  // <select> and its <label>, scan/template buttons) must not also open the file
  // dialog — otherwise picking from the dropdown would trigger both at once.
  zone.addEventListener("click", (e) => {
    if (e.target instanceof Element && e.target.closest("input, select, option, label, button")) return;
    onBrowse();
  });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); zone.classList.remove("dragover");
    const files = e.dataTransfer.files;
    if (files.length) onFile(multi ? Array.from(files) : files[0]);
  });
  input.addEventListener("change", () => {
    if (input.files.length) onFile(multi ? Array.from(input.files) : input.files[0]);
  });
}

export function resetDropZoneInput(inputId, onFile) {
  const input = document.getElementById(inputId);
  if (input) input.addEventListener("change", () => { if (input.files.length) onFile(input.files[0]); });
}

export function linkPriceInputs(unitEl, extEl, getQty) {
  unitEl.addEventListener("input", () => {
    const up = parseFloat(unitEl.value), qty = getQty();
    if (!isNaN(up) && qty > 0) extEl.value = (up * qty).toFixed(2);
  });
  extEl.addEventListener("input", () => {
    const ep = parseFloat(extEl.value), qty = getQty();
    if (!isNaN(ep) && qty > 0) unitEl.value = (ep / qty).toFixed(4);
  });
}

export function stockValueColor(stockValue, threshold) {
  if (threshold <= 0) return "#3fb950";
  const ratio = Math.min(Math.max(stockValue / threshold, 0), 1);
  const stops = STOCK_COLOR_STOPS;
  const t = ratio * 3;
  const i = Math.min(Math.floor(t), 2);
  const f = t - i;
  const a = stops[i], b = stops[i + 1];
  const r = Math.round(a.r + (b.r - a.r) * f);
  const g = Math.round(a.g + (b.g - a.g) * f);
  const bl = Math.round(a.b + (b.b - a.b) * f);
  return `rgb(${r},${g},${bl})`;
}
