// @ts-check
/* js/ui-zoom.js — live zoom state plus the coordinate-space seam.

   Owns the `--ui-zoom` custom property, persistence, and the signal other
   modules read. Arithmetic lives in ui-zoom-logic.js.

   ── The coordinate-space trap ──────────────────────────────────────────────
   `html { zoom: z }` splits the page into two px spaces, and the DOM API
   straddles them (verified against Chromium, not inferred):

     write  el.style.left = '600px'   at zoom 0.5  →  rect.left reads 300
     read   getBoundingClientRect()                →  post-zoom  (authored × z)
     read   event.clientX / clientY                →  post-zoom
     read   window.innerWidth / innerHeight        →  post-zoom
     read   offsetWidth / clientWidth              →  pre-zoom (authored)
     read   documentElement.clientWidth            →  pre-zoom (authored)

   So the usual "read a rect, clamp against innerWidth, write style.left"
   routine mixes spaces and mispositions elements at any zoom ≠ 1 — while
   looking flawless at 100%, where the two spaces coincide. Positioning code
   must work entirely in authored px: take rects from `innerRect()`, window
   bounds from `zoomedViewport()`, pointer coords through `toInnerPx()`.
   ────────────────────────────────────────────────────────────────────────── */

import { signal } from './signals.js';
import {
  DEFAULT_ZOOM, clampToStep, stepZoom, normalizePersistedZoom, viewportFor, scaleRect,
} from './ui-zoom-logic.js';

/* Deliberately NOT importing store.js. Leaf modules like part-preview.js and
   text-popover.js import this for the geometry helpers, and store.js pulls in
   js/constants.js, whose top-level `await fetch` crashes vitest collection (see
   the CLAUDE.md trap). Persistence is injected instead, by app-init.js. */

/** @type {((zoom: number) => void)|null} */
let _persist = null;

/**
 * Register how a zoom change is persisted. Called once by app-init.js, which
 * owns the store; until then, changes apply without being saved.
 * @param {(zoom: number) => void} fn
 */
export function setZoomPersister(fn) { _persist = fn; }

/** @type {{ get(): number, set(v: number): void, peek(): number }} */
export const zoomSignal = signal(DEFAULT_ZOOM);

/** @returns {number} the live zoom factor (1 = 100%) */
export function getZoom() { return zoomSignal.peek(); }

/**
 * Set the zoom level. Throws on a non-finite argument — that is a programming
 * error, and substituting a default would bury it.
 * @param {number} z
 * @param {{ persist?: boolean }} [opts] `persist: false` applies without writing
 *   preferences (used when applying an already-persisted value at startup).
 */
export function setZoom(z, opts = {}) {
  const next = clampToStep(z);
  document.documentElement.style.setProperty('--ui-zoom', String(next));
  zoomSignal.set(next);
  if (opts.persist !== false && _persist) _persist(next);
}

export function zoomIn() { setZoom(stepZoom(getZoom(), 1)); }
export function zoomOut() { setZoom(stepZoom(getZoom(), -1)); }
export function resetZoom() { setZoom(DEFAULT_ZOOM); }

/**
 * Apply a persisted zoom value. Called once, right after preferences load and
 * before the grid renders, so the UI never visibly jumps from 100% to the stored
 * level. Repairs missing or malformed stored values rather than throwing.
 * @param {unknown} stored the raw `preferences.ui_zoom`
 */
export function applyStoredZoom(stored) {
  setZoom(normalizePersistedZoom(stored), { persist: false });
}

/**
 * Window bounds in authored px — the space of `offsetWidth` and of any px you
 * write. Use this instead of `window.innerWidth`/`innerHeight` in positioning
 * code.
 * @returns {{ w: number, h: number }}
 */
export function zoomedViewport() {
  return viewportFor(window.innerWidth, window.innerHeight, getZoom());
}

/**
 * An element's box in authored px, so it can be compared against `offsetWidth`
 * and assigned to `style.left`/`style.top` without a space mismatch.
 * Identity at 100% zoom.
 * @param {Element} el
 * @returns {import('./ui-zoom-logic.js').RectLike}
 */
export function innerRect(el) {
  return scaleRect(el.getBoundingClientRect(), 1 / getZoom());
}

/**
 * Convert a post-zoom length (a `clientX`/`clientY` reading, or a raw rect
 * dimension) into authored px.
 * @param {number} px
 * @returns {number}
 */
export function toInnerPx(px) { return px / getZoom(); }
