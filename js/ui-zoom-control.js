// @ts-check
/* js/ui-zoom-control.js — header zoom widget:  [🔍−] ──●── [🔍+]  100%

   Every control writes through ui-zoom.js and the whole widget re-renders from
   zoomSignal, so the keyboard shortcuts visibly move the slider and the readout
   can never drift from the applied zoom. */

import { effect } from './signals.js';
import { zoomSignal, setZoom, zoomIn, zoomOut, resetZoom } from './ui-zoom.js';
import { ZOOM_STEPS, zoomIndex, zoomFromIndex, zoomPercent } from './ui-zoom-logic.js';
import { AppLog } from './api.js';

export function initZoomControl() {
  const slider = /** @type {HTMLInputElement|null} */ (document.getElementById('zoom-slider'));
  const pct = document.getElementById('zoom-percent');
  const outBtn = document.getElementById('zoom-out');
  const inBtn = document.getElementById('zoom-in');
  if (!slider || !pct || !outBtn || !inBtn) {
    AppLog.warn('Zoom control: header markup missing; Ctrl+-/+ still work');
    return;
  }

  // The slider's value is an index into ZOOM_STEPS, not a percentage: dragging
  // can then only ever land on a level Ctrl+-/+ can also reach. Set max from the
  // ladder so index.html's hardcoded max cannot silently drift out of range.
  slider.max = String(ZOOM_STEPS.length - 1);

  slider.addEventListener('input', () => setZoom(zoomFromIndex(Number(slider.value))));
  outBtn.addEventListener('click', () => zoomOut());
  inBtn.addEventListener('click', () => zoomIn());
  pct.addEventListener('click', () => resetZoom());

  effect(() => {
    const z = zoomSignal.get();
    slider.value = String(zoomIndex(z));
    pct.textContent = zoomPercent(z) + '%';
    outBtn.toggleAttribute('disabled', z === ZOOM_STEPS[0]);
    inBtn.toggleAttribute('disabled', z === ZOOM_STEPS[ZOOM_STEPS.length - 1]);
  });
}
