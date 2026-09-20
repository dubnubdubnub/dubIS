// @ts-check
/* jlc-sessions.js — the JLCPCB section of the Preferences modal: the paired
   accounts, a Revoke per account, and the pairing code that authorises the
   jlc-bridge browser extension to hand a session over.

   Split out of preferences-modal.js for the same reason server-list.js was: it
   is a live section, with a countdown and a poll that must start when the
   modal opens and stop when it closes. Every decision lives in
   js/jlc-logic.js; this file is DOM wiring plus that lifecycle.

   Why a poll at all: the handshake ends at the *extension*, which POSTs to
   `/v1/distributors/jlcpcb/session` on its own. Nothing notifies this window,
   so while a code is live the panel re-reads the session list and notices the
   new account appear. It runs only while a code is live and the modal is open.

   The flow, from docs/plans/2026-09-20-extension-credential-capture.md:

     Sign in  ──► POST /pairing ──► {nonce, ttl}
                                     the user pastes the nonce into the
                                     extension popup; the extension validates
                                     with JLC and POSTs the session here.
*/

import { api, AppLog } from './api.js';
import { showToast, escHtml } from './ui-helpers.js';
import {
  sessionRows,
  statusLine,
  pairingFromResponse,
  countdown,
  newAccounts,
  PAIRING_STEPS,
  EXTENSION_UNVERIFIABLE,
} from './jlc-logic.js';

/** Countdown resolution. */
const TICK_MS = 1000;
/** How often, while a code is live, to re-read the session list. */
const POLL_MS = 3000;

/** @type {{code: string, expiresAtMs: number, before: Set<string>} | null} */
let pairing = null;
/** @type {ReturnType<typeof setInterval> | null} */
let tickTimer = null;
let ticks = 0;
/** Bumped whenever the panel stops, so an in-flight fetch cannot write to a
 *  closed modal (same guard as server-list.js's render generation). */
let generation = 0;

// ── Elements ──────────────────────────────────────────────

function el(id) {
  return document.getElementById(id);
}

// ── Render ────────────────────────────────────────────────

/**
 * Paint the account list and the status line from a sessions payload.
 *
 * Renders from `sessionRows()` only — never from the payload — so the
 * whitelist in js/jlc-logic.js is what reaches the DOM (threat-model rule 4:
 * no credential is ever displayed).
 * @param {any} status
 */
export function renderJlcSessions(status) {
  const statusEl = el('jlc-status');
  if (statusEl) statusEl.textContent = statusLine(status);

  const host = el('jlc-accounts');
  if (!host) return;
  const rows = sessionRows(status && status.accounts, Date.now());
  host.innerHTML = rows.length
    ? rows.map(rowHtml).join('')
    : '<div class="jlc-empty">No accounts paired yet.</div>';
  return rows;
}

/** @param {{account: string, label: string, addedAt: string, lastOk: string, addedAgo: string, lastOkAgo: string}} row */
function rowHtml(row) {
  return `
    <div class="jlc-row" data-account="${escHtml(row.account)}">
      <span class="jlc-ident">
        <span class="jlc-account">${escHtml(row.account)}</span>
        <span class="jlc-label">${escHtml(row.label || 'no label')}</span>
      </span>
      <span class="jlc-stamps" title="added ${escHtml(row.addedAt)} &middot; last ok ${escHtml(row.lastOk)}">
        added ${escHtml(row.addedAgo)} &middot; last ok ${escHtml(row.lastOkAgo)}
      </span>
      <button class="btn btn-sm jlc-revoke" data-act="revoke" title="Forget this account&rsquo;s stored session">Revoke</button>
    </div>`;
}

/** @param {{code: string, expiresAtMs: number}} live */
function pairingHtml(live) {
  const steps = PAIRING_STEPS.map((s) => `<li>${escHtml(s)}</li>`).join('');
  return `
    <div class="jlc-code-row">
      <code class="jlc-code" id="jlc-code">${escHtml(live.code)}</code>
      <button class="btn btn-sm" data-act="copy">Copy</button>
      <span class="jlc-countdown" id="jlc-countdown">${escHtml(countdown(live.expiresAtMs, Date.now()).text)}</span>
    </div>
    <ol class="jlc-steps">${steps}</ol>
    <span class="jlc-note">${escHtml(EXTENSION_UNVERIFIABLE)}</span>`;
}

function clearPairing() {
  pairing = null;
  stopTicking();
  const host = el('jlc-pairing');
  if (host) {
    host.innerHTML = '';
    host.classList.add('hidden');
  }
  const btn = el('jlc-signin');
  if (btn) /** @type {HTMLButtonElement} */ (btn).disabled = false;
}

// ── Lifecycle ─────────────────────────────────────────────

/** Load the account list. Called when the Preferences modal opens. */
export function startJlcPanel() {
  const round = ++generation;
  clearPairing();
  const statusEl = el('jlc-status');
  if (statusEl) statusEl.textContent = statusLine(null);
  return api('list_jlc_sessions').then((status) => {
    if (round !== generation) return;
    renderJlcSessions(status);
  });
}

/** Stop the countdown and the arrival poll. Called when the modal closes. */
export function stopJlcPanel() {
  generation += 1;
  clearPairing();
}

function stopTicking() {
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
  ticks = 0;
}

function startTicking() {
  stopTicking();
  tickTimer = setInterval(onTick, TICK_MS);
}

function onTick() {
  if (!pairing) {
    stopTicking();
    return;
  }
  const state = countdown(pairing.expiresAtMs, Date.now());
  const label = el('jlc-countdown');
  if (label) label.textContent = state.text;
  if (state.expired) {
    // The nonce is single-use and short-lived by design; the honest move when
    // it lapses is to say so and offer a new one, not to keep polling.
    stopTicking();
    const btn = el('jlc-signin');
    if (btn) /** @type {HTMLButtonElement} */ (btn).disabled = false;
    return;
  }
  ticks += 1;
  if (ticks % Math.round(POLL_MS / TICK_MS) === 0) pollForArrival();
}

/** Re-read the session list and notice an account the extension just added. */
function pollForArrival() {
  const round = generation;
  const live = pairing;
  if (!live) return;
  api('list_jlc_sessions').then((status) => {
    if (round !== generation || pairing !== live) return;
    const rows = renderJlcSessions(status) || [];
    const arrived = newAccounts(rows, live.before);
    if (arrived.length === 0) return;
    clearPairing();
    const name = arrived.join(', ');
    showToast('JLC account paired: ' + name);
    AppLog.info('JLC: paired account ' + name);
  });
}

// ── Wiring ────────────────────────────────────────────────

/** Bind the section's controls. Called once, at module init. */
export function wireJlcPanel() {
  const signin = el('jlc-signin');
  if (signin) signin.addEventListener('click', onSignIn);

  const accounts = el('jlc-accounts');
  if (accounts) accounts.addEventListener('click', onAccountsClick);

  const pairingHost = el('jlc-pairing');
  if (pairingHost) pairingHost.addEventListener('click', onPairingClick);
}

function onSignIn() {
  const btn = /** @type {HTMLButtonElement | null} */ (el('jlc-signin'));
  if (btn) btn.disabled = true;
  const round = generation;
  // Snapshot before minting, so an account that arrives during THIS pairing is
  // distinguishable from the ones already stored.
  const before = new Set(sessionRows(currentRowsSource(), Date.now()).map((r) => r.account));
  api('create_jlc_pairing').then((result) => {
    if (round !== generation) return;
    const minted = pairingFromResponse(result, Date.now());
    if (!minted.ok) {
      if (btn) btn.disabled = false;
      showToast(minted.reason);
      AppLog.warn('JLC: ' + minted.reason);
      return;
    }
    pairing = { code: minted.code, expiresAtMs: minted.expiresAtMs, before };
    const host = el('jlc-pairing');
    if (host) {
      host.innerHTML = pairingHtml(pairing);
      host.classList.remove('hidden');
    }
    startTicking();
  });
}

/** The accounts currently on screen, read back out of the DOM so the snapshot
 *  needs no second fetch. */
function currentRowsSource() {
  const host = el('jlc-accounts');
  if (!host) return [];
  return Array.from(host.querySelectorAll('.jlc-row')).map((rowEl) => ({
    account: /** @type {HTMLElement} */ (rowEl).dataset.account || '',
  }));
}

/** @param {Event} e */
function onPairingClick(e) {
  const btn = /** @type {HTMLElement | null} */ (
    /** @type {HTMLElement} */ (e.target).closest('[data-act="copy"]')
  );
  if (!btn || !pairing) return;
  if (navigator.clipboard) navigator.clipboard.writeText(pairing.code);
  showToast('Pairing code copied');
}

/** @param {Event} e */
function onAccountsClick(e) {
  const btn = /** @type {HTMLElement | null} */ (
    /** @type {HTMLElement} */ (e.target).closest('[data-act="revoke"]')
  );
  if (!btn) return;
  const rowEl = btn.closest('.jlc-row');
  if (!(rowEl instanceof HTMLElement)) return;
  const account = rowEl.dataset.account || '';
  if (!account) return;
  // Destructive and unrecoverable without signing in again, so it asks —
  // the same bar the server picker's Remove uses.
  if (!window.confirm('Forget the stored JLC session for ' + account + '?')) return;
  const round = generation;
  api('revoke_jlc_session', account).then((result) => {
    if (round !== generation) return;
    if (!result) return;
    // The revoke response carries the remaining accounts, so the list is
    // repainted from the server's own answer rather than from a local splice.
    renderJlcSessions({ supported: true, accounts: result.accounts });
    showToast('Revoked JLC session for ' + account);
    AppLog.info('JLC: revoked session for ' + account);
  });
}
