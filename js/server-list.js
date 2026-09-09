/* server-list.js — the server picker in the Preferences modal: the roster of
   dubIS servers, a reachability dot per row, and the select-then-restart flow.

   Split out of preferences-modal.js because it is the only section of that
   modal with a live component (a polling loop that has to start when the modal
   opens and stop when it closes). All decisions live in js/servers-logic.js;
   this file is DOM wiring plus the poll lifecycle.
*/

// @ts-check

import { AppLog } from './api.js';
import { showToast, escHtml } from './ui-helpers.js';
import {
  getServers,
  addServer,
  updateServer,
  removeServer,
  selectServer,
  getServerUrl,
} from './store.js';
import {
  LOCAL_ID,
  serverRows,
  probePlan,
  classifyProbe,
  selectionStatus,
} from './servers-logic.js';
import { probeServer } from './server-probe.js';

/* How often the dots re-check while the modal is open. Only runs while the
   section is visible, so this is a handful of requests during the seconds a
   user spends choosing — not a background poll. */
const POLL_MS = 8000;

/** @type {ReturnType<typeof setInterval> | null} */
let pollTimer = null;
/** Bumped on every render so a probe that resolves after a re-render is dropped. */
let generation = 0;

const DOT_TITLES = {
  active: 'connected to this server now',
  live: 'reachable',
  down: 'unreachable',
  foreign: 'something answered, but it is not dubIS',
  blocked: 'cannot be checked from an https page',
  dormant: 'starts with the app',
  checking: 'checking…',
};

// ── Render ────────────────────────────────────────────────

function listEl() {
  return document.getElementById('pref-server-list');
}

/**
 * Rebuild the roster rows from the store, then kick off a probe round.
 * Called on open, and after every add/edit/remove/select.
 */
export function renderServerList() {
  const host = listEl();
  if (!host) return;
  generation += 1;
  const rows = serverRows(getServers(), getServerUrl());
  host.innerHTML = rows.map(rowHtml).join('');
  syncSelectionStatus();
  probeAll();
}

/**
 * @param {{id: string, name: string, url: string, selected: boolean, removable: boolean, unlisted: boolean}} row
 */
function rowHtml(row) {
  const isLocal = row.id === LOCAL_ID;
  const urlText = isLocal ? 'spawned by this app' : row.url;
  return `
    <div class="server-row${row.selected ? ' selected' : ''}" data-server-id="${escHtml(row.id)}" data-server-url="${escHtml(row.url)}">
      <span class="server-dot checking" data-role="dot" title="${escHtml(DOT_TITLES.checking)}"></span>
      <span class="server-ident">
        <span class="server-name" data-role="name">${escHtml(row.name)}</span>
        <span class="server-url">${escHtml(urlText)}</span>
      </span>
      <span class="server-detail" data-role="detail"></span>
      ${row.selected
        ? '<span class="server-badge">selected</span>'
        : `<button class="btn btn-sm server-select" data-act="select" ${row.unlisted ? 'disabled' : ''}>Use</button>`}
      ${row.removable
        ? '<button class="btn btn-sm server-edit" data-act="edit" title="Rename or change the URL">Edit</button>'
          + '<button class="btn btn-sm server-remove" data-act="remove" title="Remove from the list">&times;</button>'
        : '<span class="server-row-spacer"></span>'}
    </div>`;
}

function syncSelectionStatus() {
  const status = document.getElementById('pref-server-status');
  if (!status) return;
  const { pending, text } = selectionStatus(getServerUrl(), window.location.origin);
  status.textContent = text;
  status.style.color = pending ? 'var(--color-yellow)' : 'var(--text-muted)';
  const restart = document.getElementById('pref-restart');
  if (restart) restart.classList.toggle('pending', pending);
}

// ── Probing ───────────────────────────────────────────────

function setDot(rowEl, state, detail) {
  const dot = rowEl.querySelector('[data-role="dot"]');
  const detailEl = rowEl.querySelector('[data-role="detail"]');
  if (dot) {
    dot.className = 'server-dot ' + state;
    dot.setAttribute('title', DOT_TITLES[state] || state);
  }
  if (detailEl) detailEl.textContent = detail || '';
}

/** Probe every rendered row, in parallel, ignoring results from a stale render. */
function probeAll() {
  const host = listEl();
  if (!host) return;
  const round = generation;
  const rows = /** @type {NodeListOf<HTMLElement>} */ (host.querySelectorAll('.server-row'));
  rows.forEach((rowEl) => {
    // From the data attribute, not the visible text: the local row displays
    // prose ("spawned by this app") where its URL would be.
    const plan = probePlan({ url: rowEl.dataset.serverUrl || '' }, window.location.origin);
    if (plan.state !== 'probe') {
      setDot(rowEl, plan.state, plan.detail || '');
      return;
    }
    probeServer(plan.url).then((result) => {
      // A re-render (or a modal close) happened while this was in flight; its
      // row element is detached, and writing to it would resurrect a dot for a
      // server that may no longer be listed.
      if (round !== generation) return;
      const { state, detail } = classifyProbe(result);
      setDot(rowEl, state, state === 'live' ? result.ms + ' ms' : detail);
    });
  });
}

// ── Lifecycle ─────────────────────────────────────────────

/** Start rendering + polling. Called when the Preferences modal opens. */
export function startServerList() {
  renderServerList();
  stopPolling();
  pollTimer = setInterval(probeAll, POLL_MS);
}

/** Stop polling. Called when the modal closes — nothing to check when hidden. */
export function stopServerList() {
  stopPolling();
  // Invalidate in-flight probes so their `.then` cannot touch a hidden modal.
  generation += 1;
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// ── Wiring ────────────────────────────────────────────────

/** Bind the roster's controls. Called once, at module init. */
export function wireServerList() {
  const host = listEl();
  if (host) host.addEventListener('click', onRowClick);

  const addBtn = document.getElementById('pref-server-add');
  if (addBtn) addBtn.addEventListener('click', onAdd);

  const urlInput = document.getElementById('pref-server-new-url');
  if (urlInput) {
    urlInput.addEventListener('keydown', (e) => {
      if (/** @type {KeyboardEvent} */ (e).key === 'Enter') {
        e.preventDefault();
        onAdd();
      }
    });
  }
  const nameInput = document.getElementById('pref-server-new-name');
  if (nameInput) {
    nameInput.addEventListener('keydown', (e) => {
      if (/** @type {KeyboardEvent} */ (e).key === 'Enter') {
        e.preventDefault();
        document.getElementById('pref-server-new-url')?.focus();
      }
    });
  }

  const restart = document.getElementById('pref-restart');
  if (restart) restart.addEventListener('click', onRestart);
}

function onAdd() {
  const nameInput = /** @type {HTMLInputElement | null} */ (document.getElementById('pref-server-new-name'));
  const urlInput = /** @type {HTMLInputElement | null} */ (document.getElementById('pref-server-new-url'));
  if (!urlInput) return;
  const result = addServer(nameInput ? nameInput.value : '', urlInput.value);
  if (!result.ok) {
    showToast(result.reason);
    return;
  }
  urlInput.value = '';
  if (nameInput) nameInput.value = '';
  renderServerList();
}

/** @param {Event} e */
function onRowClick(e) {
  const btn = /** @type {HTMLElement | null} */ (
    /** @type {HTMLElement} */ (e.target).closest('[data-act]')
  );
  if (!btn) return;
  const rowEl = btn.closest('.server-row');
  if (!(rowEl instanceof HTMLElement)) return;
  const id = rowEl.dataset.serverId || '';
  const act = btn.dataset.act;

  if (act === 'select') {
    const result = selectServer(id);
    if (!result.ok) {
      showToast(result.reason);
      renderServerList();
      return;
    }
    renderServerList();
    offerRestart(result.url);
    return;
  }
  if (act === 'remove') {
    const target = getServers().find((s) => s.id === id);
    if (!target) return;
    // A window.confirm blocks the webview's event loop, which is exactly why
    // the rest of this app avoids it — but removal is destructive and the
    // existing restart flow already uses one, so this stays consistent.
    if (!window.confirm('Remove “' + target.name + '” from the server list?')) return;
    const { deselected } = removeServer(id);
    renderServerList();
    if (deselected) {
      showToast('Removed the selected server — falling back to local on next launch');
      offerRestart('');
    }
    return;
  }
  if (act === 'edit') onEdit(id);
}

/** @param {string} id */
function onEdit(id) {
  const target = getServers().find((s) => s.id === id);
  if (!target) return;
  const name = window.prompt('Name for this server:', target.name);
  if (name === null) return;
  const url = window.prompt('URL for this server:', target.url);
  if (url === null) return;
  const result = updateServer(id, { name, url });
  if (!result.ok) {
    showToast(result.reason);
    return;
  }
  renderServerList();
}

/**
 * A selection only takes effect at launch, so say so and offer the restart
 * right where the choice was made — a "selected" badge that changes nothing
 * until the user finds the restart button on their own is the worse outcome.
 * @param {string} url "" for local
 */
function offerRestart(url) {
  const label = url || 'the local server';
  if (!window.confirm('Restart dubIS now to connect to ' + label + '?')) {
    showToast('Saved — restart dubIS to connect to ' + label);
    return;
  }
  doRestart();
}

function onRestart() {
  const target = getServerUrl() || 'the local server';
  if (!window.confirm('Restart dubIS now to connect to ' + target + '?')) return;
  doRestart();
}

async function doRestart() {
  // `window.pywebview` is injected by the desktop host and has no ambient
  // type; every other caller of it lives in a file without `// @ts-check`.
  const shell = /** @type {any} */ (window).pywebview?.api;
  if (!shell || typeof shell.restart_app !== 'function') {
    // In a browser tab there is no desktop process to relaunch. Say so rather
    // than appearing to do nothing.
    showToast('Restart is only available in the desktop app — reopen it to apply');
    return;
  }
  try {
    await shell.restart_app();
  } catch (e) {
    AppLog.error('preferences: restart_app failed: ' + e.message);
    showToast('Could not restart — close and reopen dubIS to apply');
  }
}
