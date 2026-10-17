/* Drag-to-resize panels — and the ONLY writer of the three panels' inline
   `flex`/`width` (tests/js/panel-geometry-owner.test.js enforces that).

   Collapse (js/panel-collapse.js) used to save and restore these same inline
   styles around a collapse, so two modules each believed they owned the panel
   sizes, and the fractional flex-grow this module wrote is what left a dead
   strip beside the BOM when a panel was hidden. Now collapse is purely the
   `.panel-collapsed` class (its `!important` zero width overrides the inline
   weight without touching it), and the weight a panel had before collapsing is
   simply still there when the class comes off. The arithmetic, and why weights
   are px rather than fractions, lives in js/panel-layout-logic.js.

   All geometry here is in *authored* px — the space of the weights and of the
   minimums. That means every rect read goes through innerRect() and every
   pointer delta through toInnerPx(): under the root UI zoom, rects and clientX
   come back post-zoom, so feeding them straight in would resize panels by the
   wrong amount (and silently move the effective minimum) at any zoom != 1. See
   js/ui-zoom.js. */
'use strict';

import { innerRect, toInnerPx } from './ui-zoom.js';
import {
  PANEL_MIN_WIDTHS, flexFor, canDrag, rebaseWeights, clampDrag,
} from './panel-layout-logic.js';

const CONSOLE_MIN_HEIGHT = 100;            // authored px

/** @type {HTMLElement[]} */
let panels;
/** @type {HTMLElement} */
let consoleLog;

/** Null until the first drag: until then the stylesheet sizes every panel.
 *  @type {import('./panel-layout-logic.js').Weights} */
let weights = [null, null, null];

/** Read from the class panel-collapse.js renders — the DOM is the collapse state's
 *  single rendering, and importing that module would drag store.js in here. */
const collapsedNow = () => panels.map(p => p.classList.contains('panel-collapsed'));

/** @param {import('./panel-layout-logic.js').Weights} next */
function applyWeights(next) {
  const flex = next.map(flexFor);   // validate all three before writing any
  weights = next;
  for (let i = 0; i < panels.length; i++) {
    panels[i].style.flex = flex[i];
    panels[i].style.width = '';
  }
}

/**
 * @param {HTMLElement} handle
 * @param {number} leftIdx
 */
function initHDrag(handle, leftIdx) {
  const rightIdx = leftIdx + 1;

  handle.addEventListener('pointerdown', function (e) {
    const collapsed = collapsedNow();
    // A divider beside a collapsed panel does not drag (see canDrag). Bail before
    // preventDefault/capture so the gesture is left entirely alone.
    if (!canDrag(leftIdx, collapsed)) return;

    e.preventDefault();
    handle.setPointerCapture(e.pointerId);

    const widths = panels.map(p => innerRect(p).width);
    // Visible panels take their current widths, so the drag starts with the row
    // exactly as it looks; collapsed ones keep their remembered share.
    const base = rebaseWeights(weights, widths, collapsed);
    applyWeights(base);

    const startX = e.clientX;
    const startLeft = widths[leftIdx];
    const startRight = widths[rightIdx];

    handle.classList.add('active');
    document.body.classList.add('resizing');

    /** @param {PointerEvent} ev */
    function onMove(ev) {
      const { left, right } = clampDrag(startLeft, startRight,
        toInnerPx(ev.clientX - startX),
        PANEL_MIN_WIDTHS[leftIdx], PANEL_MIN_WIDTHS[rightIdx]);
      const next = base.slice();
      next[leftIdx] = left;
      next[rightIdx] = right;
      applyWeights(next);
    }

    function onUp() {
      handle.classList.remove('active');
      document.body.classList.remove('resizing');
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      // Nothing to convert: the weights written during the drag are already the
      // final, collapse-safe encoding.
    }

    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
  });
}

/* ── Horizontal handles (between panels) ─────────────── */
function createHHandle(leftIdx) {
  const h = document.createElement('div');
  h.className = 'resize-handle-h';
  panels[leftIdx].after(h);
  return h;
}

export function init() {
  panels = ['panel-import', 'panel-inventory', 'panel-bom'].map(id => {
    const node = document.getElementById(id);
    if (!node) throw new Error(`resize-panels: #${id} is missing`);
    return node;
  });
  const log = document.getElementById('console-log');
  if (!log) throw new Error('resize-panels: #console-log is missing');
  consoleLog = log;
  weights = [null, null, null];

  const hHandle1 = createHHandle(0);
  const hHandle2 = createHHandle(1);

  initHDrag(hHandle1, 0);
  initHDrag(hHandle2, 1);

  /* ── Vertical handle (above console log) ─────────────── */
  const vHandle = document.createElement('div');
  vHandle.className = 'resize-handle-v';
  consoleLog.before(vHandle);

  vHandle.addEventListener('pointerdown', function (e) {
    e.preventDefault();
    vHandle.setPointerCapture(e.pointerId);

    const startY = e.clientY;
    const startH = innerRect(consoleLog).height;
    const panelH = innerRect(panels[0]).height;
    const headerH = innerRect(panels[0].querySelector('.panel-header')).height;
    const importBody = document.getElementById('import-body');
    const dropZone = importBody ? importBody.querySelector('.drop-zone') : null;
    const reservedH = headerH + (dropZone ? innerRect(dropZone).height + 48 : 120);
    const maxH = panelH - reservedH;

    vHandle.classList.add('active');
    document.body.classList.add('resizing-v');

    function onMove(ev) {
      const dy = toInnerPx(startY - ev.clientY);
      let newH = startH + dy;
      if (newH < CONSOLE_MIN_HEIGHT) newH = CONSOLE_MIN_HEIGHT;
      if (newH > maxH) newH = maxH;
      consoleLog.style.height = newH + 'px';
    }

    function onUp() {
      vHandle.classList.remove('active');
      document.body.classList.remove('resizing-v');
      vHandle.removeEventListener('pointermove', onMove);
      vHandle.removeEventListener('pointerup', onUp);
    }

    vHandle.addEventListener('pointermove', onMove);
    vHandle.addEventListener('pointerup', onUp);
  });

  // Handed to js/panel-collapse.js, which mounts its toggle pills onto these —
  // returning them beats re-querying by DOM position.
  return { hHandles: [hHandle1, hHandle2], vHandle };
}
