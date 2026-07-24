// @ts-check
/* distributor-fetch.js — low-level per-distributor product fetch, extracted
   from inv-modals.js (Task 2 of refactor-sweep-2) so fetch-controller.js and
   any other caller can depend on this without pulling in modal code. */

import { API_MAP } from '../api-map.js';

// ── Fetch-price suppliers: item key → label + backend method ──
export const FETCH_SUPPLIERS = [
  { key: "lcsc", label: "LCSC", method: "fetch_lcsc_product" },
  { key: "digikey", label: "Digikey", method: "fetch_digikey_product" },
  { key: "mouser", label: "Mouser", method: "fetch_mouser_product" },
  { key: "pololu", label: "Pololu", method: "fetch_pololu_product" },
];

/**
 * Fetch a single distributor product WITHOUT going through api() — the
 * per-row fetch loop below relies on failures staying scoped to that row
 * (no global error toast; see fetchRow). Mirrors api()'s own API_MAP-driven
 * URL building (same as js/api.js's `callHttp`) but skips api()'s
 * catch→toast wrapper entirely, so a thrown error here is the caller's to
 * handle per-row.
 *
 * `method` is one of FETCH_SUPPLIERS' `fetch_<distributor>_product` names —
 * each aliases the generated `fetch_distributor_product` route with a fixed
 * distributor segment baked into `API_MAP[method].path` (e.g.
 * `/v1/distributors/lcsc/product/{code}`), so building the URL from the
 * method's own map entry — same pattern api-map aliases always use — needs
 * no separate distributor-name argument.
 *
 * @param {string} method
 * @param {string} code
 * @returns {Promise<any>}
 */
export async function fetchDistributorProduct(method, code) {
  const entry = API_MAP[method];
  const url = entry.path.replace("{code}", encodeURIComponent(code));
  const res = await fetch(url, { method: entry.verb });
  if (!res.ok) {
    let message = res.statusText || ("HTTP " + res.status);
    try {
      const errBody = await res.json();
      if (errBody && errBody.error) message = errBody.error;
    } catch {
      // Non-JSON error body — fall back to statusText/status.
    }
    throw new Error(message);
  }
  const data = await res.json();
  return entry.unwrap ? data[entry.unwrap] : data;
}
