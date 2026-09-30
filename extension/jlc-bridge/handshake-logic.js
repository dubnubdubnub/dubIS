// Pure decisions for the bridge's handshakes — no `chrome`, no `fetch`.
//
// Kept apart from background.js so tests can import them directly: which
// handshake a pasted code starts, how a DigiKey sign-in probe is read, and the
// field shape a cookie is sent in. Everything here takes plain values and
// returns plain values.

/**
 * dubIS mints every DigiKey pairing code with this literal prefix and never
 * starts a JLC code with it, so the code alone decides the flow — the popup has
 * one box and no distributor picker to get wrong.
 */
export const DIGIKEY_CODE_PREFIX = "DK-";

/**
 * @param {string} code  an already-trimmed pairing code
 * @returns {"digikey"|"jlc"}
 */
export function routeForCode(code) {
  return String(code ?? "").startsWith(DIGIKEY_CODE_PREFIX) ? "digikey" : "jlc";
}

/**
 * Cloudflare's "checking your browser" page. It is served with 200 or 403 at
 * the URL that was asked for, so neither the status nor the final URL gives it
 * away — only the body does. Seeing it proves nothing about sign-in.
 *
 * @param {string} body
 * @returns {boolean}
 */
export function isCloudflareInterstitial(body) {
  const text = String(body ?? "");
  if (/performing security verification/i.test(text)) return true;
  const title = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(text);
  return Boolean(title && /just a moment/i.test(title[1]));
}

/**
 * Whether a URL is where DigiKey sends someone who is not signed in.
 *
 * Verified live on 2026-09-29: a signed-out visit to /MyDigiKey ends on
 * `https://auth.digikey.com/as/authorization.oauth2?...`, which contains
 * neither "/login" nor "/signin". Mirrors `digikey_session.is_login_url`.
 *
 * @param {string} url
 * @returns {boolean}
 */
export function isDigikeyLoginUrl(url) {
  const lowered = String(url ?? "").toLowerCase();
  let host = "";
  try {
    host = new URL(lowered).hostname;
  } catch {
    host = "";
  }
  return (
    host === "auth.digikey.com" ||
    lowered.includes("authorization.oauth2") ||
    lowered.includes("/login") ||
    lowered.includes("/signin")
  );
}

/**
 * Read one DigiKey account-page probe.
 *
 * "signed_in" is the only answer that ends the wait, so it must be earned: a
 * 2xx whose final (post-redirect) URL is not a login page and whose body is not
 * a Cloudflare interstitial. Anything the probe cannot vouch for — a 403/5xx, a
 * challenge page, a missing URL — is "inconclusive": keep polling, and never
 * mistake it for a session.
 *
 * @param {{ok: boolean, status: number, url: string, body: string}} probe
 * @returns {{state: "signed_in"|"signed_out"|"inconclusive", reason: string}}
 */
export function classifyDigikeyProbe({ ok, status, url, body }) {
  if (isCloudflareInterstitial(body)) {
    return { state: "inconclusive", reason: `HTTP ${status}, Cloudflare check page` };
  }
  const finalUrl = String(url ?? "").toLowerCase();
  if (status === 401 || isDigikeyLoginUrl(finalUrl)) {
    let where = "";
    try {
      where = new URL(finalUrl).host;
    } catch {
      where = "";
    }
    return {
      state: "signed_out",
      reason: `DigiKey answered as signed out (HTTP ${status}${where ? ` at ${where}` : ""})`,
    };
  }
  if (!ok) {
    return { state: "inconclusive", reason: `HTTP ${status}` };
  }
  if (!finalUrl) {
    return { state: "inconclusive", reason: `HTTP ${status}, no final URL` };
  }
  return { state: "signed_in", reason: `HTTP ${status}` };
}

/**
 * The field shape a cookie is POSTed in — the same for both distributors, so
 * the server reads one format. `expirationDate` is omitted for session cookies.
 *
 * @param {{name: string, value: string, domain: string, path: string, secure?: boolean, httpOnly?: boolean, expirationDate?: number}} cookie
 */
export function toPushedCookie(cookie) {
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
}

/**
 * Turn a dubIS rejection into the sentence the popup shows. dubIS answers
 * errors as `{error, code, detail}`, and the CORS grant on the intake routes
 * makes that body readable here, so prefer its `error` over a raw dump.
 *
 * @param {number} status
 * @param {string} text  the response body, possibly empty or not JSON
 * @returns {string}
 */
export function describeRejection(status, text) {
  const raw = String(text ?? "");
  let message = "";
  try {
    const body = JSON.parse(raw);
    if (body && typeof body.error === "string" && body.error) {
      message = body.code ? `${body.error} (${body.code})` : body.error;
    }
  } catch {
    message = "";
  }
  if (!message) message = raw.slice(0, 300);
  return `dubIS rejected the DigiKey session (HTTP ${status})${message ? `: ${message}` : ""}`;
}
