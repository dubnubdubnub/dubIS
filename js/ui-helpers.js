/* ui-helpers.js — DOM utility functions shared across panels */

import { trap, release } from './a11y/focus-trap.js';

let _enterSubmitEnabled = () => true;
export function setEnterSubmitEnabled(fn) { _enterSubmitEnabled = fn; }

const TOAST_DURATION_MS = 2500;

const STOCK_COLOR_STOPS = [
  { r: 248, g: 81, b: 73 },   // #f85149  red
  { r: 240, g: 136, b: 62 },  // #f0883e  orange
  { r: 210, g: 153, b: 34 },  // #d29922  yellow
  { r: 63, g: 185, b: 80 },   // #3fb950  green
];

export function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), TOAST_DURATION_MS);
}

export function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
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
