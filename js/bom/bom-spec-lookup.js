// @ts-check
/* bom/bom-spec-lookup.js — learn what a BOM row IS from its LCSC code.

   Why this exists: a KiCad BOM exported with Value = the MPN and no
   description column ("CL05B104KB54PNC", "0402WGF1001TCE") carries no
   electrical value anywhere in its text. js/matching.js cannot propose a
   substitute for a part whose value it cannot know — and must not guess one
   (see the evidence rule at the top of matching.js). But every such row carries
   an LCSC code, and the distributor knows the value, voltage and package. This
   module asks, once per code per session, and hands the answer to the matcher
   as `bom.spec`.

   Pure and DOM-free: the fetcher, the logger and the clock are injected, so the
   whole contract — which rows are asked about, the cache, the bounded
   concurrency, what counts as a failure — is unit-testable without the app.
   js/bom/bom-panel.js owns the wiring and the re-match.

   What is cached, and for how long:
     found      for the session. A product's capacitance does not change.
     not found  for the session. A normal answer, not a failure: obsolete and
                mistyped codes exist, and asking again will not change it.
     failed     for RETRY_MS. A network error or a 5xx says nothing about the
                part, so it is retried — but not on every inventory refresh,
                which would turn one flaky network into a warning storm (and a
                warning reopens the collapsed log panel). */

/** @typedef {import('../types.js').BomSpec} BomSpec */

/** Match types that identify the part itself; a row matched this way is never looked up. */
export const IDENTITY_MATCH_TYPES = new Set(['lcsc', 'mpn', 'manual', 'confirmed']);

export const DEFAULT_CONCURRENCY = 4;
export const RETRY_MS = 60_000;

const LCSC_CODE = /^C\d+$/;

/**
 * The LCSC codes worth looking up for a set of match results: rows that are
 * not identity-matched (missing, value, fuzzy, generic), not DNP, with a
 * well-formed code. Deduplicated, in result order.
 * @param {Array<{bom: any, matchType: string|null}>|null|undefined} results
 * @returns {string[]}
 */
export function codesNeedingSpec(results) {
  if (!results) return [];
  const out = [];
  const seen = new Set();
  for (const r of results) {
    if (!r || !r.bom || r.bom.dnp) continue;
    if (IDENTITY_MATCH_TYPES.has(r.matchType || '')) continue;
    const code = String(r.bom.lcsc || '').trim().toUpperCase();
    if (!LCSC_CODE.test(code) || seen.has(code)) continue;
    seen.add(code);
    out.push(code);
  }
  return out;
}

/**
 * Project a distributor product onto the fields matching reads. Everything
 * else (prices, stock, image) is left behind: `bom.spec` is evidence about
 * what the part is, not a quote.
 * @param {string} code
 * @param {any} product a DistributorProduct
 * @returns {BomSpec}
 */
export function specFromProduct(code, product) {
  const str = (v) => (typeof v === 'string' ? v : '');
  const attributes = Array.isArray(product.attributes)
    ? product.attributes
      .filter((a) => a && typeof a.name === 'string')
      .map((a) => ({ name: a.name, value: str(a.value) }))
    : [];
  return {
    source: 'lcsc',
    code,
    mpn: str(product.mpn),
    manufacturer: str(product.manufacturer),
    description: str(product.description),
    package: str(product.package),
    category: str(product.category),
    subcategory: str(product.subcategory),
    attributes,
  };
}

/**
 * Whether an error from the fetcher means "this code does not exist", as
 * opposed to "we could not ask". The /v1 route answers an unknown code with
 * 404 `{code: "product_not_found"}`; js/api.js's apiRequest carries both onto
 * the thrown Error.
 * @param {any} err
 */
export function isNotFoundError(err) {
  return !!err && (err.code === 'product_not_found' || err.status === 404);
}

/**
 * @typedef {{state: 'found', spec: BomSpec}
 *   | {state: 'not_found'}
 *   | {state: 'failed', message: string, at: number}} CacheEntry
 */

/**
 * @typedef {Object} LookupOutcome
 * @property {string[]} asked      codes actually fetched this run
 * @property {string[]} found      codes that answered with a product
 * @property {string[]} notFound   codes the distributor does not know
 * @property {{code: string, message: string}[]} failed  codes we could not ask about
 */

/**
 * @param {{
 *   fetchProduct: (code: string) => Promise<any>,
 *   log?: {info: (m: string) => void, warn: (m: string) => void},
 *   concurrency?: number,
 *   now?: () => number,
 *   retryMs?: number,
 * }} opts
 *   `fetchProduct` resolves a product, resolves null/undefined for an unknown
 *   code, or throws (an Error with `code: 'product_not_found'` / `status: 404`
 *   also means unknown; anything else is a failure).
 */
export function createSpecLookup(opts) {
  const fetchProduct = opts.fetchProduct;
  if (typeof fetchProduct !== 'function') {
    throw new TypeError('createSpecLookup: fetchProduct must be a function');
  }
  const log = opts.log || { info: () => {}, warn: () => {} };
  const concurrency = Math.max(1, opts.concurrency || DEFAULT_CONCURRENCY);
  const now = opts.now || Date.now;
  const retryMs = opts.retryMs ?? RETRY_MS;

  /** @type {Map<string, CacheEntry>} */
  const cache = new Map();
  /** @type {Map<string, Promise<CacheEntry>>} */
  const inFlight = new Map();

  /** @param {string} code */
  function isSettled(code) {
    const e = cache.get(code);
    if (!e) return false;
    if (e.state !== 'failed') return true;
    return now() - e.at < retryMs;
  }

  /** @param {string} code @returns {Promise<CacheEntry>} */
  function fetchOne(code) {
    const pending = inFlight.get(code);
    if (pending) return pending;
    const p = (async () => {
      /** @type {CacheEntry} */
      let entry;
      try {
        const product = await fetchProduct(code);
        entry = product ? { state: 'found', spec: specFromProduct(code, product) } : { state: 'not_found' };
      } catch (err) {
        entry = isNotFoundError(err)
          ? { state: 'not_found' }
          : { state: 'failed', message: (err && err.message) || String(err), at: now() };
      }
      cache.set(code, entry);
      return entry;
    })();
    inFlight.set(code, p);
    p.finally(() => inFlight.delete(code));
    return p;
  }

  return {
    /**
     * Attach every cached spec to the aggregated BOM entries it describes.
     * Called before EVERY match — the aggregated map is rebuilt from raw rows
     * on each re-match, so an attachment made once would not survive an edit.
     * Sets the separate `spec` field only; the row's own columns are untouched.
     * @param {Map<string, any>|Iterable<any>} aggregated
     */
    apply(aggregated) {
      const entries = aggregated instanceof Map ? aggregated.values() : aggregated;
      for (const bom of entries) {
        const code = String(bom.lcsc || '').trim().toUpperCase();
        const e = cache.get(code);
        if (e && e.state === 'found') bom.spec = e.spec;
        else delete bom.spec;
      }
    },

    /**
     * Fetch the codes not yet settled in the cache, at most `concurrency` at
     * a time, and report what was learned. Logs one summary line per outcome
     * kind — not one per code — so a 40-line BOM with a dead network is one
     * warning, not forty.
     * @param {string[]} codes
     * @returns {Promise<LookupOutcome>}
     */
    async lookup(codes) {
      const todo = [...new Set(codes)].filter((c) => !isSettled(c));
      /** @type {LookupOutcome} */
      const outcome = { asked: todo, found: [], notFound: [], failed: [] };
      if (todo.length === 0) return outcome;

      let next = 0;
      async function worker() {
        while (next < todo.length) {
          const code = todo[next++];
          const entry = await fetchOne(code);
          if (entry.state === 'found') outcome.found.push(code);
          else if (entry.state === 'not_found') outcome.notFound.push(code);
          else outcome.failed.push({ code, message: entry.message });
        }
      }
      await Promise.all(Array.from({ length: Math.min(concurrency, todo.length) }, worker));

      if (outcome.notFound.length) {
        log.info('BOM spec lookup: LCSC does not know ' + outcome.notFound.join(', ')
          + ' — ' + (outcome.notFound.length === 1 ? 'that row stays' : 'those rows stay')
          + ' unmatched unless its own columns identify it');
      }
      if (outcome.failed.length) {
        const first = outcome.failed[0];
        log.warn('BOM spec lookup failed for ' + outcome.failed.length + ' of ' + todo.length
          + ' LCSC code(s) (' + outcome.failed.map((f) => f.code).join(', ') + '): ' + first.message
          + ' — those rows are matched on their own columns only; retrying in '
          + Math.round(retryMs / 1000) + 's');
      }
      return outcome;
    },

    /** @param {string} code @returns {CacheEntry|undefined} */
    peek(code) {
      return cache.get(String(code).toUpperCase());
    },

    clear() {
      cache.clear();
    },
  };
}
