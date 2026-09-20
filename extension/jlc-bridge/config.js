// Shared configuration for the dubIS JLC bridge.
//
// The only thing configurable is *which dubIS* a session may be pushed to.
// Everything else (which cookies, which validation endpoint) is a constant
// on purpose — see extension/jlc-bridge/README.md and the threat-model rules
// in docs/plans/2026-09-20-extension-credential-capture.md.

export const DEFAULT_BASE_URL = "http://127.0.0.1:7897";

/** Where the session is POSTed, relative to the configured dubIS base URL. */
export const SESSION_PATH = "/v1/distributors/jlcpcb/session";

const BASE_URL_KEY = "dubisBaseUrl";

/**
 * Validate and canonicalise a dubIS base URL.
 *
 * Throws rather than falling back to a default: a typo'd base URL must be a
 * loud failure, not a silent push to somewhere else.
 *
 * @param {string} raw
 * @returns {string} origin + path, no trailing slash
 */
export function normalizeBaseUrl(raw) {
  const text = String(raw ?? "").trim();
  if (!text) {
    throw new Error("dubIS base URL is empty — set one on the options page.");
  }
  let parsed;
  try {
    parsed = new URL(text);
  } catch {
    throw new Error(`dubIS base URL is not a URL: ${text}`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`dubIS base URL must be http:// or https://, got ${parsed.protocol}`);
  }
  if (parsed.search || parsed.hash) {
    throw new Error("dubIS base URL must not carry a query string or fragment.");
  }
  const path = parsed.pathname.replace(/\/+$/, "");
  return `${parsed.origin}${path}`;
}

/**
 * Read the configured dubIS base URL, falling back to the local default.
 * @returns {Promise<string>}
 */
export async function getBaseUrl() {
  const stored = await chrome.storage.local.get(BASE_URL_KEY);
  const raw = stored[BASE_URL_KEY];
  if (!raw) return DEFAULT_BASE_URL;
  return normalizeBaseUrl(raw);
}

/**
 * Persist a dubIS base URL. Validates first, so bad input never lands.
 * @param {string} raw
 * @returns {Promise<string>} the normalized value that was stored
 */
export async function setBaseUrl(raw) {
  const normalized = normalizeBaseUrl(raw);
  await chrome.storage.local.set({ [BASE_URL_KEY]: normalized });
  return normalized;
}
