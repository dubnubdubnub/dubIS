/* inventory/inv-import-markers.js — green dots in a thin gutter marking
   recently-imported parts, fading per import generation. Display-only. */

import state, { generationOpacityFor } from './inv-state.js';

let gutter = null;

function ensureGutter() {
  const panel = document.getElementById('panel-inventory');
  const body = state.body;
  if (!panel || !body) return null;
  if (!gutter || gutter.parentElement !== panel) {
    gutter = document.createElement('div');
    gutter.className = 'inv-import-gutter';
    panel.appendChild(gutter);
  }
  // Align the gutter with the scroll container's visible box.
  gutter.style.top = body.offsetTop + 'px';
  gutter.style.height = body.clientHeight + 'px';
  return gutter;
}

/** Recompute dots from the rows currently in the DOM. Call after each render. */
export function refreshImportMarkers() {
  const g = ensureGutter();
  if (!g) return;
  g.innerHTML = '';
  if (!state.importGenerations.length) return;
  const body = state.body;
  const scrollH = body.scrollHeight || 1;
  const seen = new Set();
  body.querySelectorAll('.inv-part-row[data-part-id]').forEach(row => {
    const key = (row.dataset.partId || '').toUpperCase();
    if (!key || seen.has(key)) return;
    const opacity = generationOpacityFor(key);
    if (opacity <= 0) return;
    seen.add(key);
    const dot = document.createElement('div');
    dot.className = 'inv-import-dot';
    dot.style.top = ((row.offsetTop / scrollH) * 100) + '%';
    dot.style.opacity = String(opacity);
    g.appendChild(dot);
  });
}
