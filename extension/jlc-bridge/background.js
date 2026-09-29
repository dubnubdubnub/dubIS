// dubIS bridge — service worker.
//
// The whole extension is one push: on a user click in the popup, wait until
// the user is genuinely signed in to JLCPCB or DigiKey, then hand that site's
// session cookies to the dubIS the user configured, together with the pairing
// nonce dubIS displayed. The nonce itself picks the site: DigiKey codes start
// with "DK-" (handshake-logic.js), everything else is JLC.
//
// Load-bearing properties (docs/plans/2026-09-20-extension-credential-capture.md):
//   * No `externally_connectable` and no content scripts, so no web page can
//     reach this worker. The only senders are this extension's own pages.
//   * There is no "fetch this URL" message. The only outbound URLs are the one
//     JLC validation endpoint, the one DigiKey account page, and the configured
//     dubIS intake routes.
//   * JLC cookies are filtered HERE, by name, against one domain. DigiKey sends
//     its whole digikey.com jar, deliberately — see DIGIKEY_COOKIE_DOMAIN.
//     A cookie value is never stored in extension storage or logged.
//   * Nothing runs without a nonce the user pasted in from dubIS.

import { DIGIKEY_PUSH_PATH, getBaseUrl, SESSION_PATH } from "./config.js";
import {
  classifyDigikeyProbe,
  describeRejection,
  routeForCode,
  toPushedCookie,
} from "./handshake-logic.js";

/**
 * The only cookies that may ever leave this extension. A short allowlist, not
 * a filter over whatever the jar happens to hold (rule 6: filter at source).
 */
const SESSION_COOKIE_NAMES = ["JLCPCB_SESSION_ID"];

/** chrome.cookies is scoped by host_permissions, which is only jlcpcb.com. */
const COOKIE_DOMAIN = "jlcpcb.com";

const VALIDATE_ENDPOINT =
  "https://jlcpcb.com/api/overseas-smt-component-order-platform/v1" +
  "/overseasSmtComponentOrder/myLibrary/getCustomerComponentStock";

/**
 * JLC's "not signed in" application code. An anonymous request to this
 * endpoint still MINTS a fresh JLCPCB_SESSION_ID, so the presence of a cookie
 * proves nothing — this response code is the only valid signal, and waiting
 * for the cookie instead harvests an anonymous session every single time.
 */
const NOT_SIGNED_IN_CODE = 460;

const POLL_ATTEMPTS = 40;
const POLL_INTERVAL_MS = 3000;

const STATUS_KEY = "runStatus";

/** In-memory guard so two popup clicks cannot start two overlapping runs. */
let activeRun = null;

/** @typedef {{state: string, message: string, attempt: number, attempts: number, at: string}} RunStatus */

/**
 * @param {Partial<RunStatus>} patch
 * @returns {Promise<void>}
 */
async function setStatus(patch) {
  const status = {
    state: "idle",
    message: "",
    attempt: 0,
    attempts: POLL_ATTEMPTS,
    ...patch,
    at: new Date().toISOString(),
  };
  await chrome.storage.session.set({ [STATUS_KEY]: status });
}

/** @returns {Promise<RunStatus>} */
async function getStatus() {
  const stored = await chrome.storage.session.get(STATUS_KEY);
  return (
    stored[STATUS_KEY] || {
      state: "idle",
      message: "Idle.",
      attempt: 0,
      attempts: POLL_ATTEMPTS,
      at: new Date().toISOString(),
    }
  );
}

/**
 * Reject anything that is not a plausible pairing nonce before it is used.
 * @param {unknown} raw
 * @returns {string}
 */
function requireNonce(raw) {
  const nonce = String(raw ?? "").trim();
  if (!nonce) {
    throw new Error("Paste the pairing code dubIS showed you first.");
  }
  if (nonce.length > 512 || /[\s\u0000-\u001f]/.test(nonce)) {
    throw new Error("That does not look like a dubIS pairing code.");
  }
  return nonce;
}

/** @param {number} ms */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Keep the MV3 service worker from being evicted mid-poll.
 *
 * A poll of 40 x 3s is two minutes, well past the 30s idle shutdown, and this
 * extension deliberately has no `alarms` permission to resume from. Calling an
 * extension API resets the idle timer, which is what this is for.
 */
async function keepAlive() {
  await chrome.runtime.getPlatformInfo();
}

/**
 * One validation request.
 *
 * @returns {Promise<{code: number|null, account: string|null, reason: string}>}
 *   `code` is null when the probe could not run (network error, non-JSON body,
 *   5xx) — inconclusive, which means keep polling, never "signed in".
 */
async function probeSignedIn() {
  const url = `${VALIDATE_ENDPOINT}?pageNum=1&pageSize=1&keyWord=&_t=${Date.now()}`;
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  } catch (err) {
    return { code: null, account: null, reason: `network error: ${err.message}` };
  }

  let body;
  try {
    body = await response.json();
  } catch {
    return { code: null, account: null, reason: `HTTP ${response.status}, non-JSON body` };
  }

  const code = typeof body?.code === "number" ? body.code : null;
  if (code === null) {
    return { code: null, account: null, reason: `HTTP ${response.status}, no application code` };
  }

  const first = Array.isArray(body?.data?.list) ? body.data.list[0] : null;
  const account = first && first.customerCode ? String(first.customerCode) : null;
  return { code, account, reason: `code ${code}` };
}

/**
 * Wait until JLC answers with anything other than "not signed in".
 * @returns {Promise<{account: string|null}>}
 */
async function waitForSignIn(run) {
  let lastReason = "never ran";
  for (let attempt = 1; attempt <= POLL_ATTEMPTS; attempt += 1) {
    if (run.cancelled) throw new Error("Cancelled.");
    await keepAlive();
    await setStatus({
      state: "polling",
      attempt,
      message: `Waiting for you to sign in to JLCPCB (check ${attempt}/${POLL_ATTEMPTS})…`,
    });

    const { code, account, reason } = await probeSignedIn();
    lastReason = reason;
    if (code !== null && code !== NOT_SIGNED_IN_CODE) {
      return { account };
    }

    if (attempt < POLL_ATTEMPTS) await sleep(POLL_INTERVAL_MS);
  }
  throw new Error(
    `Gave up after ${POLL_ATTEMPTS} checks over ` +
      `${Math.round((POLL_ATTEMPTS * POLL_INTERVAL_MS) / 1000)}s — JLCPCB still says ` +
      `not signed in (${lastReason}). Sign in at jlcpcb.com in this browser, then try again.`
  );
}

/**
 * Read only the allowlisted session cookies for jlcpcb.com.
 *
 * Values are returned for the single POST that follows and are never stored,
 * never logged and never put in extension storage.
 *
 * @returns {Promise<Array<{name: string, value: string, domain: string, path: string, secure: boolean, httpOnly: boolean, expirationDate?: number}>>}
 */
async function readSessionCookies() {
  const jar = await chrome.cookies.getAll({ domain: COOKIE_DOMAIN });
  const wanted = jar.filter((cookie) => SESSION_COOKIE_NAMES.includes(cookie.name));
  if (wanted.length === 0) {
    throw new Error(
      `Signed in, but no ${SESSION_COOKIE_NAMES.join("/")} cookie is present for ` +
        `${COOKIE_DOMAIN}. Nothing was sent.`
    );
  }
  return wanted.map((cookie) => {
    const out = {
      name: cookie.name,
      value: cookie.value,
      domain: cookie.domain,
      path: cookie.path,
      secure: Boolean(cookie.secure),
      httpOnly: Boolean(cookie.httpOnly),
    };
    if (typeof cookie.expirationDate === "number") {
      out.expirationDate = cookie.expirationDate;
    }
    return out;
  });
}

/**
 * POST the session to the configured dubIS. Write-only: the response is read
 * for success/failure only, and dubIS never hands a credential back.
 */
async function pushToDubis(nonce, account, cookies) {
  const base = await getBaseUrl();
  const url = `${base}${SESSION_PATH}`;
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      credentials: "omit",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nonce, account, cookies }),
    });
  } catch (err) {
    throw new Error(`Could not reach dubIS at ${url} — ${err.message}`);
  }

  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.text()).slice(0, 300);
    } catch {
      detail = "";
    }
    throw new Error(`dubIS rejected the session (HTTP ${response.status})${detail ? `: ${detail}` : ""}`);
  }
  return { url, account };
}

/**
 * The whole flow, started by a popup click and nothing else.
 * @param {string} nonceRaw
 */
async function runHandshake(nonceRaw) {
  if (activeRun && !activeRun.cancelled) {
    throw new Error("A send is already running.");
  }
  const nonce = requireNonce(nonceRaw);
  const run = { cancelled: false };
  activeRun = run;

  try {
    const { account } = await waitForSignIn(run);
    await setStatus({ state: "pushing", message: "Signed in — sending the session to dubIS…" });
    const cookies = await readSessionCookies();
    const result = await pushToDubis(nonce, account, cookies);
    await setStatus({
      state: "ok",
      message: account
        ? `Sent. dubIS accepted the session for account ${account}.`
        : "Sent. dubIS accepted the session.",
    });
    return result;
  } catch (err) {
    await setStatus({ state: "error", message: err.message });
    throw err;
  } finally {
    if (activeRun === run) activeRun = null;
  }
}

// ── DigiKey (phase 2) ────────────────────────────────────────────────────────

/**
 * Every digikey.com cookie is sent, not a named allowlist — a deliberate
 * departure from rule 6 of docs/plans/2026-09-20-extension-credential-capture.md.
 * Nobody has yet recorded which DigiKey cookies make up a session (it sits
 * behind Cloudflare and rotates several), and guessing short would push a
 * session that silently fails. dubIS logs the cookie *names* it receives, so
 * the list can be narrowed to a JLC-style allowlist once it is known. Still one
 * domain only, and host_permissions stops Chrome reading any other.
 */
const DIGIKEY_COOKIE_DOMAIN = "digikey.com";

/**
 * A signed-out request here redirects to DigiKey's sign-in page, so the final
 * URL answers "am I signed in?" without reading any account data.
 */
// Signed in: 200 "Index - My DigiKey". Signed out: redirected to
// auth.digikey.com. Verified live 2026-09-29; /MyDigiKey/Account, the old
// probe, now 404s for a signed-in user, so a real session never read as signed
// in. Must match `digikey_session.ACCOUNT_URL` (a test compares the two).
const DIGIKEY_ACCOUNT_URL = "https://www.digikey.com/MyDigiKey";

/**
 * One DigiKey probe. The body is read only to spot a Cloudflare interstitial
 * and is never stored or logged.
 *
 * @returns {Promise<{state: "signed_in"|"signed_out"|"inconclusive", reason: string}>}
 */
async function probeDigikeySignedIn() {
  let response;
  try {
    response = await fetch(DIGIKEY_ACCOUNT_URL, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      redirect: "follow",
    });
  } catch (err) {
    return { state: "inconclusive", reason: `network error: ${err.message}` };
  }
  let body = "";
  try {
    body = await response.text();
  } catch {
    body = "";
  }
  return classifyDigikeyProbe({
    ok: response.ok,
    status: response.status,
    url: response.url,
    body,
  });
}

/**
 * Wait until the DigiKey account page loads as a signed-in user. Same cadence,
 * keep-alive and cancel support as the JLC wait.
 */
async function waitForDigikeySignIn(run) {
  let lastReason = "never ran";
  let everConclusive = false;
  for (let attempt = 1; attempt <= POLL_ATTEMPTS; attempt += 1) {
    if (run.cancelled) throw new Error("Cancelled.");
    await keepAlive();
    await setStatus({
      state: "polling",
      attempt,
      message: `Waiting for you to sign in to DigiKey (check ${attempt}/${POLL_ATTEMPTS})…`,
    });

    const { state, reason } = await probeDigikeySignedIn();
    lastReason = reason;
    if (state === "signed_in") return;
    if (state === "signed_out") everConclusive = true;

    if (attempt < POLL_ATTEMPTS) await sleep(POLL_INTERVAL_MS);
  }
  const span = `${POLL_ATTEMPTS} checks over ${Math.round((POLL_ATTEMPTS * POLL_INTERVAL_MS) / 1000)}s`;
  if (!everConclusive) {
    throw new Error(
      `Gave up after ${span} — no check could tell whether you are signed in ` +
        `(last: ${lastReason}). DigiKey may be showing a Cloudflare check that needs a ` +
        `real visit: open digikey.com in a tab, sign in, then try again.`
    );
  }
  throw new Error(
    `Gave up after ${span} — DigiKey still says not signed in (${lastReason}). ` +
      `Sign in at digikey.com in this browser, then try again.`
  );
}

/**
 * Read every digikey.com cookie (see DIGIKEY_COOKIE_DOMAIN for why all of them).
 * Values live only for the single POST that follows.
 */
async function readDigikeyCookies() {
  const jar = await chrome.cookies.getAll({ domain: DIGIKEY_COOKIE_DOMAIN });
  if (jar.length === 0) {
    throw new Error(
      `Signed in, but this browser holds no cookies for ${DIGIKEY_COOKIE_DOMAIN}. Nothing was sent.`
    );
  }
  return jar.map(toPushedCookie);
}

/**
 * POST the DigiKey session to the configured dubIS. Write-only, like the JLC
 * push: the response is read for the verdict, never for a credential.
 *
 * @returns {Promise<{url: string, state: string, cookieCount: number|null}>}
 */
async function pushDigikeyToDubis(nonce, cookies) {
  const base = await getBaseUrl();
  const url = `${base}${DIGIKEY_PUSH_PATH}`;
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      credentials: "omit",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nonce, cookies }),
    });
  } catch (err) {
    throw new Error(`Could not reach dubIS at ${url} — ${err.message}`);
  }

  if (!response.ok) {
    let text = "";
    try {
      text = await response.text();
    } catch {
      text = "";
    }
    throw new Error(describeRejection(response.status, text));
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return {
    url,
    state: body && typeof body.state === "string" ? body.state : "valid",
    cookieCount: body && typeof body.cookie_count === "number" ? body.cookie_count : null,
  };
}

/**
 * The DigiKey flow: same shape, same single-run guard and nonce check as JLC.
 * @param {string} nonceRaw
 */
async function runDigikeyHandshake(nonceRaw) {
  if (activeRun && !activeRun.cancelled) {
    throw new Error("A send is already running.");
  }
  const nonce = requireNonce(nonceRaw);
  const run = { cancelled: false };
  activeRun = run;

  try {
    await waitForDigikeySignIn(run);
    await setStatus({ state: "pushing", message: "Signed in — sending the DigiKey session to dubIS…" });
    const cookies = await readDigikeyCookies();
    const result = await pushDigikeyToDubis(nonce, cookies);
    await setStatus({
      state: "ok",
      message:
        result.state === "unverified"
          ? "Sent. dubIS accepted the DigiKey session (dubIS could not double-check it; " +
            "the extension saw you signed in)."
          : "Sent. dubIS accepted the DigiKey session.",
    });
    return result;
  } catch (err) {
    await setStatus({ state: "error", message: err.message });
    throw err;
  } finally {
    if (activeRun === run) activeRun = null;
  }
}

/**
 * Answer a popup message, tolerating the one benign failure: the popup closed
 * while a run was still going, so the port is gone. Everything else about a
 * run is still reported through the status line.
 *
 * @param {(response: object) => void} sendResponse
 * @param {object} payload
 */
function respond(sendResponse, payload) {
  try {
    sendResponse(payload);
  } catch (err) {
    console.debug("dubIS bridge: popup closed before the reply landed —", err.message);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Belt and braces: with no externally_connectable and no content scripts
  // this can only be one of this extension's own pages already.
  if (sender.id !== chrome.runtime.id) return false;

  const type = message && message.type;

  if (type === "status") {
    getStatus().then((status) => respond(sendResponse, { ok: true, status }));
    return true;
  }

  if (type === "start") {
    // The nonce lives only for the duration of this run, in this worker, and
    // its own prefix picks the site. Anything not "DK-" takes the JLC path
    // exactly as before.
    const nonceText = String(message.nonce ?? "").trim();
    const handshake = routeForCode(nonceText) === "digikey" ? runDigikeyHandshake : runHandshake;
    handshake(message.nonce)
      .then((result) => respond(sendResponse, { ok: true, result }))
      .catch((err) => respond(sendResponse, { ok: false, error: err.message }));
    return true;
  }

  if (type === "cancel") {
    if (activeRun) activeRun.cancelled = true;
    setStatus({ state: "idle", message: "Cancelled." }).then(() =>
      respond(sendResponse, { ok: true })
    );
    return true;
  }

  respond(sendResponse, { ok: false, error: `Unknown message type: ${String(type)}` });
  return false;
});
