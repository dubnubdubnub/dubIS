// scripts/serve-root-lib.mjs
//
// Pure logic behind "is the server on the Playwright port rooted at THIS
// tree?". scripts/serve-static.mjs answers GET /__serve-root with the root it
// serves; tests/js/e2e/serve-root-setup.mjs asks that question before any
// spec runs. Dependency-free (node builtins only) so vitest can unit-test it
// without Playwright — the same shape as playwright-health-lib.mjs.
//
// Why this exists: playwright.config.mjs sets reuseExistingServer:
// !process.env.CI, so a local run attaches to whatever is already listening
// on the serve port. An orphaned `node scripts/serve-static.mjs . 3123` left
// running by a finished agent — with its cwd in a *different worktree* — made
// a spec report "0 preset buttons, no tab strip in the DOM" for a feature
// that existed on main. Neither the feature nor the suite was broken; the
// run was measuring another tree, and nothing said so. The guard turns that
// silent misattribution into a loud, actionable failure. It never kills
// anything: the listener may belong to another concurrent session.

import { realpathSync } from 'node:fs';
import { resolve } from 'node:path';

/** Must match IDENTITY_PATH in scripts/serve-static.mjs. */
export const SERVE_ROOT_ENDPOINT = '/__serve-root';
export const DEFAULT_SERVE_PORT = 3123;

/** The webServer port. One definition for the config, the guard and the tests. */
export function resolveServePort(env = process.env) {
  const raw = env.SERVE_PORT ?? String(DEFAULT_SERVE_PORT);
  const port = Number.parseInt(raw, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new RangeError(`SERVE_PORT must be an integer port number, got ${JSON.stringify(env.SERVE_PORT)}`);
  }
  return port;
}

/**
 * Absolute, symlinks resolved, no trailing separator — so two spellings of
 * one directory (e.g. macOS /var vs /private/var) compare equal, and a
 * trailing slash cannot fake a mismatch.
 */
export function normalizeRoot(p) {
  let out = resolve(p);
  try {
    out = realpathSync(out);
  } catch {
    // Path absent (or vanished mid-check): compare the given spelling. A
    // nonexistent root will not equal a real one, which is the safe answer.
  }
  // Never reduce '/' or a Windows drive root ('C:\') to something that means
  // a different path ('C:' is "cwd on C:", not the drive root).
  if (out.length > 1 && !/^[A-Za-z]:[/\\]$/.test(out)) {
    out = out.replace(/[/\\]+$/, '');
  }
  return out;
}

/**
 * Ask the listener on `port` which root it serves.
 *
 * Statuses:
 *  - 'absent'      nothing is listening. Playwright will start its own
 *                  server, rooted at this tree — nothing to distrust.
 *  - 'match'       the listener reports the expected root.
 *  - 'mismatch'    the listener reports a different root.
 *  - 'no-endpoint' the listener answered, but not with an identity: an older
 *                  serve-static.mjs or a foreign server. Not a pass — an
 *                  unknown tree is exactly the incident.
 *  - 'unreachable' the listener accepted the connection but never answered.
 *
 * @param {number} port
 * @param {{expectedRoot?: string, host?: string, timeoutMs?: number,
 *          fetchImpl?: typeof fetch}} [options]
 * @returns {Promise<{status: 'absent'|'match'|'mismatch'|'no-endpoint'|'unreachable',
 *                    reportedRoot?: string, detail?: string}>}
 */
export async function probeServeRoot(port, {
  expectedRoot,
  host = '127.0.0.1',
  timeoutMs = 3000,
  fetchImpl = globalThis.fetch,
} = {}) {
  if (typeof fetchImpl !== 'function') {
    throw new Error('probeServeRoot needs a global fetch (Node 18+) or an explicit fetchImpl');
  }
  let res;
  try {
    res = await fetchImpl(`http://${host}:${port}${SERVE_ROOT_ENDPOINT}`, {
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    const code = err?.cause?.code;
    if (code === 'ECONNREFUSED') return { status: 'absent' };
    if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
      return { status: 'unreachable', detail: `no answer within ${timeoutMs}ms` };
    }
    return { status: 'unreachable', detail: err?.message ?? String(err) };
  }
  if (!res.ok) return { status: 'no-endpoint', detail: `HTTP ${res.status}` };
  let body;
  try {
    body = await res.json();
  } catch {
    return { status: 'no-endpoint', detail: 'the response was not JSON — not a serve-static.mjs that reports its root' };
  }
  const reported = body && typeof body.root === 'string' ? body.root : null;
  if (reported === null || reported === '') {
    return { status: 'no-endpoint', detail: 'the JSON carried no "root" field' };
  }
  if (expectedRoot === undefined) return { status: 'match', reportedRoot: reported };
  return normalizeRoot(reported) === normalizeRoot(expectedRoot)
    ? { status: 'match', reportedRoot: reported }
    : { status: 'mismatch', reportedRoot: reported };
}

/**
 * The loud failure: names the port, the root the server reports, this tree,
 * and how to find the offender. Never kills it — it may belong to another
 * concurrent session, and auto-killing would destroy that session's run.
 */
export function serveRootMismatchMessage({
  port,
  expectedRoot,
  status,
  reportedRoot,
  detail,
  platform = process.platform,
}) {
  const reportedLine = status === 'mismatch'
    ? `  server reports: ${reportedRoot ?? '(unknown)'}`
    : `  server reports: (nothing — ${detail ?? 'no detail'})`;
  const why = status === 'mismatch'
    ? 'It serves a different tree than the one this Playwright run is testing.'
    : 'It did not identify its root (no usable GET /__serve-root answer) — an '
      + 'older scripts/serve-static.mjs or a foreign listener. An unknown tree '
      + 'is not a pass.';
  const finders = [
    `  Find it:   lsof -nP -iTCP:${port} -sTCP:LISTEN`,
    ...(platform === 'win32'
      ? [`             (Windows: netstat -ano | findstr :${port} | findstr LISTENING)`]
      : []),
  ];
  return [
    `The static server on port ${port} is not this checkout.`,
    '',
    `  port:           ${port}`,
    reportedLine,
    `  this checkout:  ${expectedRoot}`,
    '',
    why,
    'Every E2E assertion would be made against files that are not this tree\'s —',
    'a feature missing there reads exactly like a broken feature here.',
    'Nothing was killed: the listener may belong to another concurrent session.',
    'Either stop that server, or give this run its own port:',
    `  SERVE_PORT=${port + 1} npx playwright test ...`,
    '',
    ...finders,
  ].join('\n');
}

/**
 * The guard's verdict: resolves for 'match' and 'absent', throws a loud
 * Error (serveRootMismatchMessage) for every state that means "the tree
 * being served is not this tree, or cannot be proven to be".
 *
 * @param {{port: number, expectedRoot: string, host?: string,
 *          timeoutMs?: number, fetchImpl?: typeof fetch}} options
 */
export async function assertServeRoot({ expectedRoot, ...probeOptions }) {
  const result = await probeServeRoot(probeOptions.port, { expectedRoot, ...probeOptions });
  if (result.status === 'match' || result.status === 'absent') return result;
  throw new Error(serveRootMismatchMessage({ expectedRoot, ...probeOptions, ...result }));
}
