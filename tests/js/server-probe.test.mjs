import { describe, it, expect, vi } from 'vitest';

import { probeServer } from '../../js/server-probe.js';

/** A fetch stub that records how it was called. */
function stubFetch(impl) {
  return vi.fn(impl);
}

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

describe('probeServer', () => {
  it('hits /v1/health on the given base URL', async () => {
    const fetchImpl = stubFetch(async () => jsonResponse({ ok: true }));
    await probeServer('https://x.example', { fetchImpl });
    const [url] = fetchImpl.mock.calls[0];
    expect(url).toMatch(/^https:\/\/x\.example\/v1\/health\?t=\d+\.\d+$/);
  });

  it('sends no credentials and sets no request headers', async () => {
    // Both matter for the CORS contract: `Access-Control-Allow-Origin: *`
    // cannot be read by a credentialed request, and any non-safelisted header
    // would turn this into a preflighted OPTIONS that FastAPI answers 405 —
    // which would report a healthy server as down.
    const fetchImpl = stubFetch(async () => jsonResponse({ ok: true }));
    await probeServer('https://x.example', { fetchImpl });
    const [, opts] = fetchImpl.mock.calls[0];
    expect(opts.credentials).toBe('omit');
    expect(opts.method).toBe('GET');
    expect(opts.headers).toBeUndefined();
    expect(opts.cache).toBeUndefined();
  });

  it('cache-busts uniquely even for probes in the same millisecond', async () => {
    // A render probes every row at once, so a timestamp alone collides and a
    // cached 200 could be replayed for a server that has since gone down.
    const fetchImpl = stubFetch(async () => jsonResponse({ ok: true }));
    await Promise.all([
      probeServer('https://x.example', { fetchImpl }),
      probeServer('https://x.example', { fetchImpl }),
      probeServer('https://x.example', { fetchImpl }),
    ]);
    const urls = fetchImpl.mock.calls.map((c) => c[0]);
    expect(new Set(urls).size).toBe(3);
  });

  it('reports a dubIS health payload as ok + dubis', async () => {
    const r = await probeServer('https://x.example', {
      fetchImpl: stubFetch(async () => jsonResponse({ ok: true })),
    });
    expect(r).toMatchObject({ ok: true, status: 200, dubis: true });
    expect(typeof r.ms).toBe('number');
  });

  it('reports a 200 from something that is not dubIS as ok but not dubis', async () => {
    const notDubis = await probeServer('https://x.example', {
      fetchImpl: stubFetch(async () => jsonResponse({ nginx: 'hello' })),
    });
    expect(notDubis).toMatchObject({ ok: true, dubis: false });

    const notJson = await probeServer('https://x.example', {
      fetchImpl: stubFetch(async () => ({
        ok: true,
        status: 200,
        json: async () => { throw new Error('not json'); },
      })),
    });
    expect(notJson).toMatchObject({ ok: true, dubis: false });
  });

  it('reports an error status without reading the body', async () => {
    const json = vi.fn();
    const r = await probeServer('https://x.example', {
      fetchImpl: stubFetch(async () => ({ ok: false, status: 502, json })),
    });
    expect(r).toMatchObject({ ok: false, status: 502 });
    expect(json).not.toHaveBeenCalled();
  });

  it('never throws — a rejected fetch comes back as a result', async () => {
    // Probing an offline server is the normal case, not an exception; every
    // caller reads the result object rather than catching.
    const r = await probeServer('https://x.example', {
      fetchImpl: stubFetch(async () => { throw new TypeError('Failed to fetch'); }),
    });
    expect(r.ok).toBe(false);
    expect(r.error).toBe('Failed to fetch');
  });

  it('aborts and reports a timeout when the server never answers', async () => {
    // A connection that is accepted and then goes silent would otherwise leave
    // the dot pulsing forever, reading as "checking" rather than "unreachable".
    const fetchImpl = stubFetch((url, opts) => new Promise((_resolve, reject) => {
      opts.signal.addEventListener('abort', () => {
        const err = new Error('aborted');
        err.name = 'AbortError';
        reject(err);
      });
    }));
    const r = await probeServer('https://x.example', { fetchImpl, timeoutMs: 5 });
    expect(r.ok).toBe(false);
    expect(r.error).toBe('timed out');
  });

  it('clears the timeout on a fast success, leaving no pending timer', async () => {
    vi.useFakeTimers();
    try {
      const r = await probeServer('https://x.example', {
        fetchImpl: stubFetch(async () => jsonResponse({ ok: true })),
        timeoutMs: 1000,
      });
      expect(r.ok).toBe(true);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
