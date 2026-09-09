/* server-probe.js — one cross-origin reachability check against a candidate
   dubIS server's `GET /v1/health`.

   Kept as its own module (rather than inlined in server-list.js) so the parts
   that are easy to get subtly wrong — the timeout, and the refusal to send
   credentials — are testable against a stubbed fetch.

   Why /v1/health specifically: it is already exempt from AuthMiddleware
   (server/auth.py EXEMPT_PATHS), so an unauthenticated probe of a token-gated
   remote server still gets a truthful answer instead of a 401 that would read
   as "down". server/routes/meta.py adds `Access-Control-Allow-Origin: *` to
   that one route so this cross-origin read is allowed; without it the browser
   would hide a perfectly good 200 behind a CORS error.
*/

const PROBE_PATH = '/v1/health';
const DEFAULT_TIMEOUT_MS = 4000;

/* Cache-buster counter. Date.now() alone is not enough: it has millisecond
   resolution, and a render probes every row at once — several probes land in
   the same millisecond and would share a URL, so a cached 200 could be
   replayed for a server that has since gone down. */
let probeSeq = 0;

/**
 * Probe one server.
 *
 * Never throws: a probe failing is the normal answer for an offline server, so
 * every outcome comes back as a result object for `classifyProbe` to read.
 *
 * @param {string} baseUrl origin of the candidate server, no trailing slash
 * @param {{timeoutMs?: number, fetchImpl?: typeof fetch}} [opts]
 * @returns {Promise<{ok: boolean, status?: number, dubis?: boolean, error?: string, ms: number}>}
 */
export async function probeServer(baseUrl, opts) {
  const o = opts || {};
  const timeoutMs = typeof o.timeoutMs === 'number' ? o.timeoutMs : DEFAULT_TIMEOUT_MS;
  const doFetch = o.fetchImpl || ((...args) => fetch(...args));
  const started = now();

  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  let timer = null;
  if (controller) {
    // A server that accepts the connection and then never answers would leave
    // the dot spinning forever, which reads as "still checking" rather than
    // the "unreachable" it is.
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }

  try {
    // Cache-busted with a query param rather than `cache: 'no-store'` on
    // purpose. The cache mode makes the browser attach Cache-Control/Pragma
    // request headers, and anything beyond the CORS-safelisted headers risks
    // turning this into a preflighted request — an OPTIONS /v1/health that
    // FastAPI answers 405, so the probe would report a healthy server as down.
    // A query param keeps it a simple GET, which needs no preflight.
    probeSeq += 1;
    const res = await doFetch(baseUrl + PROBE_PATH + '?t=' + Date.now() + '.' + probeSeq, {
      method: 'GET',
      // No cookies, no Authorization header. Both would make this a
      // credentialed request, and a credentialed request cannot be read under
      // `Access-Control-Allow-Origin: *` — the browser rejects the response
      // even though the server answered it. The endpoint needs no auth anyway.
      credentials: 'omit',
      signal: controller ? controller.signal : undefined,
    });
    if (!res.ok) return { ok: false, status: res.status, ms: elapsed(started) };
    let body = null;
    try {
      body = await res.json();
    } catch {
      // 200 with a non-JSON body — some other service on that port. Reported
      // as reachable-but-not-dubIS rather than as a working server.
      body = null;
    }
    return {
      ok: true,
      status: res.status,
      dubis: !!(body && body.ok === true),
      ms: elapsed(started),
    };
  } catch (e) {
    const aborted = e && (e.name === 'AbortError' || e.code === 20);
    return {
      ok: false,
      error: aborted ? 'timed out' : (e && e.message) || 'network error',
      ms: elapsed(started),
    };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function now() {
  return typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now();
}

function elapsed(started) {
  return Math.round(now() - started);
}
