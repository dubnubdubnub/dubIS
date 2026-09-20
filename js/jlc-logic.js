// @ts-check
/* jlc-logic.js — pure decisions for the JLCPCB pairing panel in Preferences.

   No DOM, no fetch, no store — js/jlc-sessions.js owns those. Split the same
   way js/servers-logic.js splits from js/server-list.js, and for the same
   reason: the interesting half here is what the panel is *allowed to claim*,
   and that half should be unit-testable without a browser.

   Two rules from the threat model (docs/plans/2026-09-20-extension-credential-
   capture.md) are enforced by this file rather than by care at each render
   site:

   1. **Rule 4 — write-only.** `sessionRow()` is a whitelist: it copies exactly
      the four public fields and nothing else. No `/v1` route returns a cookie
      today, and if one ever regressed and started to, the value would still
      not reach the DOM, because the panel renders rows, never raw payloads.
   2. **Honest copy.** dubIS cannot see whether the extension is installed —
      the extension pushes to dubIS and dubIS never calls it. So the panel says
      what to *do*, never what *is*: `statusLine()` reports only what the
      credential store knows, and `PAIRING_STEPS` are instructions, not state.
*/

/** The nonce TTL to assume when the pairing response omits one. Matches
 *  `jlc_session.NONCE_TTL_SECONDS`; the server's value always wins. */
export const PAIRING_TTL_SECONDS = 600;

/** Every field of a stored session the UI may ever read. A credential is not
 *  among them, and cannot become one by a payload growing a field. */
export const PUBLIC_SESSION_FIELDS = ['account', 'label', 'added_at', 'last_ok'];

/**
 * How long ago a timestamp was, coarsely, for a row's "added / last ok" line.
 *
 * Takes `nowMs` rather than reading the clock so the formatting is a pure
 * function of its inputs and the tests need no fake timers.
 * @param {any} iso
 * @param {number} nowMs
 * @returns {string} e.g. "just now", "14 min ago", "3 d ago", or "unknown"
 */
export function relativeAge(iso, nowMs) {
  const text = String(iso ?? '').trim();
  if (!text) return 'never';
  const then = Date.parse(text);
  if (Number.isNaN(then)) return 'unknown';
  const seconds = Math.floor((nowMs - then) / 1000);
  // A clock skew between the server that wrote the stamp and this window can
  // put it slightly in the future; "just now" is the honest reading of that,
  // not a negative age.
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return Math.floor(seconds / 60) + ' min ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + ' h ago';
  return Math.floor(seconds / 86400) + ' d ago';
}

/**
 * One stored session, projected to exactly what the panel may render.
 *
 * The whitelist is the point (rule 4): anything the payload carries that is
 * not one of `PUBLIC_SESSION_FIELDS` is dropped here and can never reach the
 * DOM.
 * @param {any} entry
 * @param {number} nowMs
 * @returns {{account: string, label: string, addedAt: string, lastOk: string, addedAgo: string, lastOkAgo: string}}
 */
export function sessionRow(entry, nowMs) {
  const src = entry && typeof entry === 'object' ? entry : {};
  const addedAt = String(src.added_at ?? '');
  const lastOk = String(src.last_ok ?? '');
  return {
    account: String(src.account ?? ''),
    label: String(src.label ?? ''),
    addedAt,
    lastOk,
    addedAgo: relativeAge(addedAt, nowMs),
    lastOkAgo: relativeAge(lastOk, nowMs),
  };
}

/**
 * Every paired account as a renderable row, in the order the server listed
 * them (it sorts by account), with unusable entries dropped rather than
 * rendered as a blank row with a Revoke button that names nothing.
 * @param {any} accounts
 * @param {number} nowMs
 * @returns {ReturnType<typeof sessionRow>[]}
 */
export function sessionRows(accounts, nowMs) {
  if (!Array.isArray(accounts)) return [];
  return accounts.map((a) => sessionRow(a, nowMs)).filter((row) => row.account !== '');
}

/**
 * The one-line status beside the Sign in button.
 *
 * Says only what the credential store knows. It deliberately never mentions
 * the extension: dubIS has no way to detect it, and "extension not installed"
 * would be a claim this window cannot make.
 * @param {any} status the `GET /v1/distributors/jlcpcb/sessions` payload
 * @returns {string}
 */
export function statusLine(status) {
  if (!status || typeof status !== 'object') return 'Checking…';
  if (status.supported === false) {
    return String(status.message || 'JLC pairing is not available on this server');
  }
  const count = sessionRows(status.accounts, 0).length;
  if (count === 0) return 'No JLC account paired';
  return count === 1 ? '1 account paired' : count + ' accounts paired';
}

/**
 * Turn a pairing response into what the panel shows, or a reason it cannot.
 *
 * `api()` swallows a failed call and resolves `undefined`, so "no response"
 * is a normal input here, not an exceptional one.
 * @param {any} result the `POST /v1/distributors/jlcpcb/pairing` payload
 * @param {number} nowMs
 * @returns {{ok: boolean, code: string, ttl: number, expiresAtMs: number, reason: string}}
 */
export function pairingFromResponse(result, nowMs) {
  const nonce = result && typeof result === 'object' ? String(result.nonce ?? '').trim() : '';
  if (!nonce) {
    return {
      ok: false,
      code: '',
      ttl: 0,
      expiresAtMs: 0,
      reason: 'dubIS did not return a pairing code — see the log for why.',
    };
  }
  const raw = Number(result.ttl);
  const ttl = Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : PAIRING_TTL_SECONDS;
  return { ok: true, code: nonce, ttl, expiresAtMs: nowMs + ttl * 1000, reason: '' };
}

/**
 * The countdown under the pairing code.
 *
 * The TTL has to cover the whole human flow — display, paste, sign in, up to
 * ~2 minutes of the extension polling JLC, then the POST — so the panel shows
 * the time left rather than a bare "expires in 10 minutes" that stops being
 * true the moment it is printed.
 * @param {number} expiresAtMs
 * @param {number} nowMs
 * @returns {{expired: boolean, secondsLeft: number, text: string}}
 */
export function countdown(expiresAtMs, nowMs) {
  const secondsLeft = Math.ceil((expiresAtMs - nowMs) / 1000);
  if (!(secondsLeft > 0)) {
    return {
      expired: true,
      secondsLeft: 0,
      text: 'This code has expired — click Sign in to JLC for a new one.',
    };
  }
  const mins = Math.floor(secondsLeft / 60);
  const secs = secondsLeft % 60;
  return {
    expired: false,
    secondsLeft,
    text: 'Good for ' + mins + ':' + String(secs).padStart(2, '0'),
  };
}

/**
 * Which accounts are new since a set was captured — how the panel notices that
 * the extension delivered, given that the extension POSTs to the server and
 * nothing tells this window about it.
 * @param {ReturnType<typeof sessionRow>[]} rows
 * @param {Set<string>} before
 * @returns {string[]}
 */
export function newAccounts(rows, before) {
  return rows.map((r) => r.account).filter((a) => !before.has(a));
}

/** The instructions shown with a live pairing code. Imperative on purpose —
 *  every line is something the user does, because dubIS can verify none of it
 *  (see `EXTENSION_UNVERIFIABLE`). */
export const PAIRING_STEPS = [
  'Sign in at jlcpcb.com in your normal browser — saved passwords, autofill and Google/Apple SSO all work as usual.',
  'Click the dubIS jlc-bridge extension’s toolbar icon.',
  'Paste the code above into the popup and press Send session to dubIS.',
  'The extension checks with JLC that you are really signed in, then hands the session over. This panel updates when it arrives.',
];

/** Why this panel never reports whether the extension is installed. The flow
 *  is push-only: the extension talks to dubIS, dubIS never talks to it, so
 *  "not installed" and "installed but not clicked" look identical from here. */
export const EXTENSION_UNVERIFIABLE =
  'dubIS cannot see your browser, so it cannot tell whether the extension is installed — '
  + 'if nothing arrives, load extension/jlc-bridge and set its dubIS URL, then try the code again.';

/** Stated at the top of the section, so the absence of a cookie anywhere in
 *  this UI reads as a design rule rather than an omission. */
export const NO_READBACK_NOTE =
  'dubIS stores the session cookie and never shows it again — no route returns one.';
