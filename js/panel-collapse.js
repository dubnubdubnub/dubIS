// @ts-check
/* js/panel-collapse.js — collapsible left / BOM / console regions.

   The two side panels collapse to zero width. Their toggle is a pill pinned to
   the top of the resize handle between panels — the "T" junction — which stays a
   real click target at zero panel width because the pill is wider than the 5px
   handle and carries an inflated hit box.

   Reopening restores the panel's pre-collapse flex ratio, so a user who dragged
   the divider does not lose that width to a collapse round-trip.

   State lives here; the reducer and the trigger→region table are pure and live in
   panel-collapse-logic.js. */

import { store, savePreferences } from './store.js';
import { AppLog, onLogEntry } from './api.js';
import {
  REGIONS, normalizeCollapsed, setRegion, toggleRegion, panelsToReopen, regionsChanged,
} from './panel-collapse-logic.js';

/** @type {Record<string, boolean>} */
let state = normalizeCollapsed(undefined);

/** Pre-collapse inline sizing per panel, so expand() restores it exactly. */
const savedSizing = /** @type {Record<string, {flex: string, width: string}>} */ ({});

/** @type {Record<string, HTMLElement>} */
const toggles = {};

/** @param {string} id */
const el = (id) => document.getElementById(id);
/** @param {string} id */
const regionFor = (id) => REGIONS.find(r => r.id === id);

function persist() {
  store.preferences.panels_collapsed = { ...state };
  savePreferences();
}

/** Apply `state` to the DOM. Pure render — never writes state, never persists. */
function render() {
  for (const r of REGIONS) {
    const node = el(r.panelId);
    const collapsed = state[r.id];

    if (node && r.id === 'console') {
      // The console is a fixed-height block inside the left panel. Hide the
      // resize handle above it too, or a collapsed console leaves an orphaned
      // drag handle that resizes nothing.
      node.classList.toggle('console-collapsed', collapsed);
      const vh = node.previousElementSibling;
      if (vh && vh.classList.contains('resize-handle-v')) {
        vh.classList.toggle('hidden', collapsed);
      }
    } else if (node && collapsed) {
      if (!savedSizing[r.id]) {
        savedSizing[r.id] = { flex: node.style.flex, width: node.style.width };
      }
      node.classList.add('panel-collapsed');
      node.setAttribute('aria-hidden', 'true');
    } else if (node) {
      node.classList.remove('panel-collapsed');
      node.removeAttribute('aria-hidden');
      const saved = savedSizing[r.id];
      if (saved) {
        // Restore the dragged width rather than snapping back to the 22%/40%
        // stylesheet defaults.
        node.style.flex = saved.flex;
        node.style.width = saved.width;
        delete savedSizing[r.id];
      }
    }

    const t = toggles[r.id];
    if (t) {
      t.setAttribute('aria-expanded', String(!collapsed));
      t.classList.toggle('is-collapsed', collapsed);
      const verb = collapsed ? 'Show ' : 'Hide ';
      t.title = verb + r.label;
      t.setAttribute('aria-label', verb + r.label);
      if (!collapsed) t.classList.remove('has-activity');
    }
  }
}

/** @param {string} id */
export function isCollapsed(id) { return !!state[id]; }

/**
 * @param {Record<string, boolean>} next
 * @returns {boolean} whether anything changed
 */
function commit(next) {
  if (!regionsChanged(state, next)) return false;   // no render, no disk write
  state = next;
  render();
  persist();
  return true;
}

/** @param {string} id */
export function collapseRegion(id) { commit(setRegion(state, id, true)); }
/** @param {string} id */
export function expandRegion(id) { commit(setRegion(state, id, false)); }
/** @param {string} id */
export function toggleRegionUI(id) { commit(toggleRegion(state, id)); }

/**
 * Mark a collapsed region as having unseen activity. Used for LOG_INFO, which
 * must not force a panel open.
 * @param {string} id
 */
export function flagActivity(id) {
  if (!state[id]) return;
  const t = toggles[id];
  if (t) t.classList.add('has-activity');
}

/**
 * Force open every region a trigger demands. Safe to call at any rate: a no-op
 * when nothing is collapsed, so an event burst cannot storm the disk.
 * @param {string} trigger
 */
export function handleTrigger(trigger) {
  const ids = panelsToReopen(trigger);
  if (!ids.length) return;
  let next = state;
  for (const id of ids) next = setRegion(next, id, false);
  if (commit(next)) AppLog.info('Reopened ' + ids.join(' + ') + ' (' + trigger + ')');
}

/** @param {{ hHandles: HTMLElement[] }} handles */
function mountPills(handles) {
  // hHandles[0] sits between #panel-import and #panel-inventory,
  // hHandles[1] between #panel-inventory and #panel-bom.
  const spec = [
    { handle: handles.hHandles[0], id: 'import', side: 'left' },
    { handle: handles.hHandles[1], id: 'bom', side: 'right' },
  ];
  for (const { handle, id, side } of spec) {
    const region = regionFor(id);
    if (!handle || !region) {
      AppLog.warn('Panel collapse: no resize handle for ' + id);
      continue;
    }
    const btn = document.createElement('button');
    btn.className = 'panel-toggle panel-toggle-' + side;
    btn.id = 'panel-toggle-' + id;
    btn.dataset.region = id;
    btn.setAttribute('aria-controls', region.panelId);
    btn.setAttribute('aria-expanded', 'true');
    btn.setAttribute('aria-label', 'Hide ' + region.label);
    // The chevron is decorative; the accessible name comes from aria-label.
    btn.innerHTML = '<span aria-hidden="true"></span>';
    // pointerdown must not reach the handle, or clicking the pill starts a drag.
    btn.addEventListener('pointerdown', e => e.stopPropagation());
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggleRegionUI(id); });
    handle.appendChild(btn);
    toggles[id] = btn;
  }

  const consoleBtn = el('console-toggle');
  if (consoleBtn) {
    consoleBtn.addEventListener('click', () => toggleRegionUI('console'));
    toggles.console = consoleBtn;
  }
}

/** @param {{ hHandles: HTMLElement[] }} handles */
export function initPanelCollapse(handles) {
  state = normalizeCollapsed(store.preferences.panels_collapsed);
  mountPills(handles);
  render();

  // A warning or error must surface even behind a collapsed console; plain info
  // only flags it. Registered through api.js's observer hook rather than having
  // api.js import this module — that would be a cycle, since we import AppLog.
  onLogEntry((level) => {
    if (level === 'warn' || level === 'error') handleTrigger('LOG_' + level.toUpperCase());
    else flagActivity('console');
  });
}
