// @ts-check
/* digikey-pairing.js — the DigiKey section of the Preferences modal: the
   sign-in status, a pairing code for the dubIS bridge extension, and Logout.

   Mirrors js/jlc-sessions.js: a live section with a countdown and a poll that
   start when the user asks for a code and stop when the modal closes. Every
   decision lives in js/digikey-logic.js; this file is DOM wiring plus that
   lifecycle.

   Why a poll: the handshake ends at the *extension*, which POSTs the session
   to dubIS on its own. Nothing notifies this window, so while a code is live
   the panel re-reads the login status and notices it flip. It runs only while
   a code is live and the modal is open.

     Sign in  ──► POST /v1/distributors/digikey/pairing ──► {nonce, ttl}
                  the user pastes the nonce into the extension popup; the
                  extension pushes the DigiKey session to dubIS.
*/

import { api, AppLog } from './api.js';
import { showToast, escHtml } from './ui-helpers.js';
import {
  pairingFromResponse,
  dkCountdown,
  loginStatusView,
  DK_PAIRING_INSTRUCTION,
  DK_POLL_MS,
} from './digikey-logic.js';

/** Countdown resolution. */
const TICK_MS = 1000;

/** @type {{code: string, expiresAtMs: number} | null} */
let pairing = null;
/** @type {ReturnType<typeof setInterval> | null} */
let tickTimer = null;
let ticks = 0;
/** One status read at a time: a slow response must not stack up behind the
 *  2s poll. */
let polling = false;
/** Bumped whenever the panel stops, so an in-flight fetch cannot write to a
 *  closed modal (same guard as jlc-sessions.js). */
let generation = 0;

function el(id) {
  return document.getElementById(id);
}

// ── Render ────────────────────────────────────────────────

/**
 * Paint the status line and the Sign in / Logout buttons.
 * @param {any} result the login-status payload
 */
export function renderDigikeyStatus(result) {
  const view = loginStatusView(result);
  const statusEl = el('dk-status');
  if (statusEl) {
    statusEl.textContent = view.text;
    statusEl.style.color = view.color;
  }
  const signin = el('dk-login');
  const logout = el('dk-logout');
  if (signin) signin.classList.toggle('hidden', view.loggedIn);
  if (logout) logout.classList.toggle('hidden', !view.loggedIn);
  if (result && result.message) AppLog.info('DK: ' + result.message);
  return view;
}

/** @param {{code: string, expiresAtMs: number}} live */
function pairingHtml(live) {
  return `
    <div class="jlc-code-row">
      <code class="jlc-code" id="dk-code">${escHtml(live.code)}</code>
      <button class="btn btn-sm" data-act="copy">Copy</button>
      <span class="jlc-countdown" id="dk-countdown">${escHtml(dkCountdown(live.expiresAtMs, Date.now()).text)}</span>
    </div>
    <span class="dk-instruction">${escHtml(DK_PAIRING_INSTRUCTION)}</span>`;
}

function hidePairing() {
  const host = el('dk-pairing');
  if (host) {
    host.innerHTML = '';
    host.classList.add('hidden');
  }
}

function clearPairing() {
  pairing = null;
  stopTicking();
  hidePairing();
  const btn = /** @type {HTMLButtonElement | null} */ (el('dk-login'));
  if (btn) btn.disabled = false;
}

// ── Lifecycle ─────────────────────────────────────────────

/** Read the login status. Called when the Preferences modal opens. */
export function startDigikeyPanel() {
  const round = ++generation;
  clearPairing();
  const statusEl = el('dk-status');
  if (statusEl) {
    statusEl.textContent = 'Checking…';
    statusEl.style.color = 'var(--text-muted)';
  }
  return api('get_digikey_login_status').then((result) => {
    if (round !== generation) return;
    renderDigikeyStatus(result);
  });
}

/** Stop the countdown and the login poll, and drop any live code. Called when
 *  the modal closes (preferences-modal.js's `stopDkPolling`). */
export function stopDigikeyPanel() {
  generation += 1;
  clearPairing();
}

function stopTicking() {
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
  ticks = 0;
  polling = false;
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
  const state = dkCountdown(pairing.expiresAtMs, Date.now());
  if (state.expired) {
    // The nonce is single-use and short-lived; say it lapsed and offer a new
    // one rather than polling on for a push that can no longer be accepted.
    clearPairing();
    const host = el('dk-pairing');
    if (host) {
      host.innerHTML = `<span class="jlc-countdown" id="dk-countdown">${escHtml(state.text)}</span>`;
      host.classList.remove('hidden');
    }
    renderDigikeyStatus({ logged_in: false });
    const statusEl = el('dk-status');
    if (statusEl) statusEl.textContent = 'Pairing code expired';
    return;
  }
  const label = el('dk-countdown');
  if (label) label.textContent = state.text;
  ticks += 1;
  if (ticks % Math.round(DK_POLL_MS / TICK_MS) === 0) pollForLogin();
}

/** Ask whether the extension's push has landed. */
function pollForLogin() {
  const round = generation;
  const live = pairing;
  if (!live || polling) return;
  polling = true;
  api('get_digikey_login_status').then((result) => {
    if (round !== generation || pairing !== live) return;
    polling = false;
    if (!result || !result.logged_in) return;
    clearPairing();
    renderDigikeyStatus(result);
    showToast('Signed in to DigiKey');
    AppLog.info('DK: session received from the bridge extension');
  });
}

// ── Wiring ────────────────────────────────────────────────

/** Bind the section's controls. Called once, at app init. */
export function wireDigikeyPanel() {
  const signin = el('dk-login');
  if (signin) signin.addEventListener('click', onSignIn);

  const logout = el('dk-logout');
  if (logout) logout.addEventListener('click', onLogout);

  const host = el('dk-pairing');
  if (host) host.addEventListener('click', onPairingClick);
}

function onSignIn() {
  const btn = /** @type {HTMLButtonElement | null} */ (el('dk-login'));
  if (btn) btn.disabled = true;
  const round = generation;
  api('create_digikey_pairing').then((result) => {
    if (round !== generation) return;
    const minted = pairingFromResponse(result, Date.now());
    if (!minted.ok) {
      if (btn) btn.disabled = false;
      showToast(minted.reason);
      AppLog.warn('DK: ' + minted.reason);
      return;
    }
    pairing = { code: minted.code, expiresAtMs: minted.expiresAtMs };
    const host = el('dk-pairing');
    if (host) {
      host.innerHTML = pairingHtml(pairing);
      host.classList.remove('hidden');
    }
    const statusEl = el('dk-status');
    if (statusEl) {
      statusEl.textContent = 'Waiting for the extension…';
      statusEl.style.color = 'var(--text-muted)';
    }
    startTicking();
  });
}

function onLogout() {
  const round = ++generation;
  clearPairing();
  api('logout_digikey').then(() => {
    if (round !== generation) return;
    renderDigikeyStatus({ logged_in: false });
    showToast('Digikey logged out');
  });
}

/** @param {Event} e */
function onPairingClick(e) {
  const btn = /** @type {HTMLElement} */ (e.target).closest('[data-act="copy"]');
  if (!btn || !pairing) return;
  if (navigator.clipboard) navigator.clipboard.writeText(pairing.code);
  showToast('Pairing code copied');
}
