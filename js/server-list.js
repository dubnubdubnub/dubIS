/* server-list.js — the server picker in the Preferences modal: the roster of
   dubIS servers, a reachability dot per row, and selecting one.

   Selecting used to save a preference and offer a restart, because which server
   the app talked to was decided once, at launch. It is not any more: the window
   is always served by the local hub and other servers are sources it fetches
   from, so `Use` switches live through the same js/store.js call the tab strip
   under the header makes (js/server-tabs.js). `client_shell.restart_app` still
   exists — it is a frozen surface — but nothing here calls it.

   Split out of preferences-modal.js because it is the only section of that
   modal with a live component (a polling loop that has to start when the modal
   opens and stop when it closes). All decisions live in js/servers-logic.js;
   this file is DOM wiring plus the poll lifecycle.
*/

// @ts-check

import { showToast, escHtml } from './ui-helpers.js';
import {
  getServers,
  getSources,
  addServer,
  updateServer,
  removeServer,
  setServerToken,
  switchActiveSource,
  getServerUrl,
} from './store.js';
import {
  LOCAL_ID,
  serverRows,
  probePlan,
  classifyProbe,
  credentialState,
  selectionStatus,
} from './servers-logic.js';
import { probeServer } from './server-probe.js';
import { refreshServerTabs } from './server-tabs.js';

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
  const rows = serverRows(getServers(), getServerUrl(), sourceStatusById());
  host.innerHTML = rows.map(rowHtml).join('');
  syncSelectionStatus();
  probeAll();
}

/**
 * The hub's per-source credential facts, keyed by source id.
 *
 * Only the hub can answer either of them: it is the hub's outbound client that
 * holds a token and authenticates to a source, never this window. `has_token`
 * is a boolean because `GET /v1/sources` does not echo the token itself.
 * @returns {Record<string, {has_token: boolean, auth: string}>}
 */
function sourceStatusById() {
  /** @type {Record<string, {has_token: boolean, auth: string}>} */
  const out = {};
  for (const s of getSources()) out[s.id] = { has_token: s.has_token, auth: s.auth };
  return out;
}

/**
 * @param {{id: string, name: string, url: string, selected: boolean, removable: boolean, unlisted: boolean, hasToken: boolean, auth: string}} row
 */
function rowHtml(row) {
  const isLocal = row.id === LOCAL_ID;
  const urlText = isLocal ? 'spawned by this app' : row.url;
  // Rendered only when there is something to say. Most rows have no credential
  // question at all, and a chip reading "nothing" on every one of them would
  // bury the one row that does.
  const cred = credentialState(row);
  const credHtml = cred.state === 'none' ? ''
    : `<span class="server-cred ${cred.state}" data-role="cred" title="${escHtml(cred.title)}">${escHtml(cred.label)}</span>`;
  return `
    <div class="server-row${row.selected ? ' selected' : ''}" data-server-id="${escHtml(row.id)}" data-server-url="${escHtml(row.url)}">
      <span class="server-dot checking" data-role="dot" title="${escHtml(DOT_TITLES.checking)}"></span>
      <span class="server-ident">
        <span class="server-name" data-role="name">${escHtml(row.name)}</span>
        <span class="server-url">${escHtml(urlText)}</span>
      </span>
      ${credHtml}
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
  // Always current, never "pending": the selection is applied before this runs.
  status.textContent = selectionStatus(getServerUrl()).text;
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
  // Then again once the hub has re-answered `GET /v1/sources`. The dots are
  // probed from this window, but the credential chips are not and cannot be:
  // only the hub knows whether it holds a token for a source and whether that
  // source let it in. Without this, a token that expired since the last roster
  // edit would keep reading "token" until something else happened to refresh.
  refreshServerTabs().then(() => { if (listEl()) renderServerList(); });
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
  const tokenInput = document.getElementById('pref-server-new-token');
  if (tokenInput) {
    tokenInput.addEventListener('keydown', (e) => {
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

}

async function onAdd() {
  const nameInput = /** @type {HTMLInputElement | null} */ (document.getElementById('pref-server-new-name'));
  const urlInput = /** @type {HTMLInputElement | null} */ (document.getElementById('pref-server-new-url'));
  const tokenInput = /** @type {HTMLInputElement | null} */ (document.getElementById('pref-server-new-token'));
  if (!urlInput) return;
  // Read and clear in the same breath. The field is write-only by design —
  // nothing can read the stored token back, so leaving it populated would show
  // a secret that may not even be the one in force, and a second Add would
  // re-send it to a different server.
  const token = tokenInput ? tokenInput.value : '';
  if (tokenInput) tokenInput.value = '';
  const result = addServer(nameInput ? nameInput.value : '', urlInput.value, token);
  if (!result.ok) {
    // `result.reason` may be a token rejection, which names the offending
    // character CLASS and never the token.
    showToast(result.reason);
    return;
  }
  urlInput.value = '';
  if (nameInput) nameInput.value = '';
  renderServerList();
  // The hub keeps its source registry in the same preferences file, so a roster
  // edit changes what GET /v1/sources answers — including the reachability the
  // tab strip's dots show and the credential facts the chips show, neither of
  // which this window can work out for itself.
  await refreshServerTabs();
  if (result.token) {
    // Separate call, and after the roster render: the roster is client state
    // the user can see immediately, while the token is a server-side write that
    // can fail on its own. A failure here leaves a listed server without a
    // credential — which is what the "needs a token" chip is for.
    const saved = await setServerToken(result.entry.id, result.token);
    if (!saved.ok) showToast(saved.reason);
    await refreshServerTabs();
  }
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
    // Live: the switch lands before the roster is redrawn, so the badge only
    // ever moves onto a server the hub actually adopted. A failure leaves the
    // store untouched, and the re-render puts the badge back where it was.
    switchActiveSource(id).then((result) => {
      if (!result.ok) showToast(result.reason);
      renderServerList();
    });
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
    refreshServerTabs();
    if (deselected) {
      // Removing the server we are reading from has to move us off it now —
      // leaving the hub pointed at an entry the user just deleted would keep
      // serving its inventory under a roster that no longer mentions it.
      switchActiveSource(LOCAL_ID).then(() => {
        showToast('Removed the selected server — now showing the local server');
        renderServerList();
      });
    }
    return;
  }
  if (act === 'edit') onEdit(id);
}

/** @param {string} id */
async function onEdit(id) {
  const target = getServers().find((s) => s.id === id);
  if (!target) return;
  const name = window.prompt('Name for this server:', target.name);
  if (name === null) return;
  const url = window.prompt('URL for this server:', target.url);
  if (url === null) return;
  // Deliberately NOT pre-filled with the current token — this window does not
  // have it and never will (it lives in server/token_store.py, for the hub's
  // own outbound requests). Blank therefore has to mean "leave it alone", which
  // is why removing one needs an explicit sentinel rather than an empty box.
  const token = window.prompt('API token for this server (blank = leave unchanged, "-" = remove):', '');
  if (token === null) return;
  const result = updateServer(id, { name, url });
  if (!result.ok) {
    showToast(result.reason);
    return;
  }
  renderServerList();
  await refreshServerTabs();
  const trimmed = token.trim();
  if (trimmed) {
    const saved = await setServerToken(id, trimmed === '-' ? '' : trimmed);
    if (!saved.ok) showToast(saved.reason);
    await refreshServerTabs();
  }
  renderServerList();
}
