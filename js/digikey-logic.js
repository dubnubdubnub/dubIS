// @ts-check
/* digikey-logic.js — pure decisions for the DigiKey sign-in panel in
   Preferences and the DigiKey hover-preview error line.

   No DOM, no fetch, no store — js/digikey-pairing.js owns those. Split the same
   way js/jlc-logic.js splits from js/jlc-sessions.js, and it reuses that
   module's nonce helpers outright, because DigiKey now signs in through the
   same handshake: the user signs in to digikey.com in their own browser, then
   pastes a dubIS pairing code into the dubIS bridge extension, which pushes the
   session here. dubIS never opens a browser for DigiKey any more.

   Same honesty rule as the JLC panel: dubIS cannot see the extension, so the
   panel says what to *do*, and reports only what `GET
   /v1/distributors/digikey/session` knows. */

import { countdown, pairingFromResponse } from './jlc-logic.js';

export { pairingFromResponse };

/** The button that mints a pairing code — named in the expired-code line. */
export const DK_SIGNIN_LABEL = 'Sign in to DigiKey';

/** How often, while a code is live and the modal is open, to ask whether the
 *  extension's push has landed. */
export const DK_POLL_MS = 2000;

/** The one-line instruction shown with a live code. Imperative: every clause
 *  is something the user does, since dubIS can verify none of it. */
export const DK_PAIRING_INSTRUCTION =
  'Sign in at digikey.com in your browser, then paste this code into the dubIS '
  + 'bridge extension and click Send.';

/**
 * The countdown under a DigiKey pairing code.
 * @param {number} expiresAtMs
 * @param {number} nowMs
 */
export function dkCountdown(expiresAtMs, nowMs) {
  return countdown(expiresAtMs, nowMs, DK_SIGNIN_LABEL);
}

/**
 * What the status line and the two buttons show for a login-status payload.
 *
 * `api()` swallows a failed call and resolves `undefined`; that is "we could
 * not ask", not "not logged in", so it gets its own wording rather than
 * quietly claiming a state.
 * @param {any} result the `GET /v1/distributors/digikey/session` payload
 * @returns {{loggedIn: boolean, known: boolean, text: string, color: string}}
 */
export function loginStatusView(result) {
  if (!result || typeof result !== 'object') {
    return {
      loggedIn: false,
      known: false,
      text: 'Could not check DigiKey sign-in — see the log',
      color: 'var(--color-red)',
    };
  }
  if (result.logged_in) {
    return { loggedIn: true, known: true, text: 'Logged in', color: 'var(--color-green)' };
  }
  return { loggedIn: false, known: true, text: 'Not logged in', color: 'var(--text-muted)' };
}

/**
 * The hover-preview error when a DigiKey product fetch came back empty.
 *
 * DigiKey product pages no longer need a login, so "log in to enable preview"
 * would be a false claim; signing in only *may* help (a session can get past
 * a bot wall an anonymous fetch hits).
 * @param {any} status the login-status payload, or undefined if it failed
 * @returns {string}
 */
export function digikeyPreviewError(status) {
  return status && status.logged_in
    ? 'Could not load product data'
    : 'Could not load product data — signing in to DigiKey in Preferences may help';
}
