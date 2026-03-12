/* ui-helpers.js — DOM utility functions shared across panels */

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

export function Modal(id, { onClose, cancelId } = {}) {
  const el = document.getElementById(id);
  function open()  { el.classList.remove("hidden"); }
  function close() { el.classList.add("hidden"); if (onClose) onClose(); }
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  if (cancelId) document.getElementById(cancelId).addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (el.classList.contains("hidden")) return;
    if (e.key === "Escape") close();
  });
  return { el, open, close };
}

export function setupDropZone(zoneId, inputId, onBrowse, onFile) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  zone.addEventListener("click", (e) => { if (e.target.tagName !== "INPUT") onBrowse(); });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); zone.classList.remove("dragover");
    if (e.dataTransfer.files.length) onFile(e.dataTransfer.files[0]);
  });
  input.addEventListener("change", () => { if (input.files.length) onFile(input.files[0]); });
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
