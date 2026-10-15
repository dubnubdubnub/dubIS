/**
 * Tests for the serve-root guard: the /__serve-root identity endpoint in
 * scripts/serve-static.mjs and the check in scripts/serve-root-lib.mjs that
 * the Playwright global setup (tests/js/e2e/serve-root-setup.mjs) runs.
 *
 * The incident these reproduce: an orphaned `node scripts/serve-static.mjs
 * . 3123` from a finished agent, cwd in a *different worktree*, plus
 * reuseExistingServer: !CI, made a local run measure that other tree — a
 * spec reported "0 preset buttons" for a feature that existed on main. So
 * these tests drive a REAL server on a scratch port: the bug lived in the
 * combination of a real listener and a real fetch, not in one function.
 * The mismatch case (brief requirement: a happy-path-only test does not
 * cover this bug) is the one that matters most.
 */
import { afterAll, describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import {
  SERVE_ROOT_ENDPOINT,
  assertServeRoot,
  normalizeRoot,
  probeServeRoot,
  resolveServePort,
} from '../../scripts/serve-root-lib.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..');
const SERVE_STATIC = join(REPO_ROOT, 'scripts', 'serve-static.mjs');

const cleanups = [];
afterAll(async () => {
  for (const close of cleanups.splice(0).reverse()) await close();
});

function scratchTree() {
  const dir = mkdtempSync(join(tmpdir(), 'dubis-serve-root-'));
  writeFileSync(join(dir, 'index.html'), '<html><body>scratch tree</body></html>');
  cleanups.push(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

/**
 * Make `linkPath` an alternate spelling of `target`.
 *
 * Windows needs a junction, not a symlink: creating a symlink demands
 * SeCreateSymbolicLinkPrivilege (or Developer Mode), and the win11 runner
 * executes as a service account, so `symlinkSync` there fails with
 * EPERM — a privilege fact about the machine, not a property of the guard.
 * A directory junction is the same path indirection (a reparse point the OS
 * follows on open, which is what realpath resolves) and needs no privilege.
 */
function linkTo(target, linkPath) {
  symlinkSync(target, linkPath, process.platform === 'win32' ? 'junction' : 'dir');
}

async function freePort() {
  const probe = net.createServer();
  await new Promise((r) => probe.listen(0, '127.0.0.1', r));
  const { port } = probe.address();
  await new Promise((r) => probe.close(r));
  return port;
}

/** Start `node scripts/serve-static.mjs <root> <port>` and wait until it answers. */
async function startServeStatic(root) {
  const port = await freePort();
  const child = spawn(process.execPath, [SERVE_STATIC, root, String(port)], {
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  cleanups.push(() => new Promise((r) => {
    child.once('exit', r);
    child.kill('SIGKILL');
  }));
  const deadline = Date.now() + 10_000;
  for (;;) {
    const res = await probeServeRoot(port, { expectedRoot: root });
    if (res.status === 'match') return { port, child, res };
    if (Date.now() > deadline) {
      throw new Error(`serve-static.mjs rooted at ${root} never reported its root on :${port}`);
    }
    await new Promise((r) => setTimeout(r, 50));
  }
}

async function startForeignServer(handler) {
  const port = await freePort();
  const server = createServer(handler);
  await new Promise((r) => server.listen(port, '127.0.0.1', r));
  cleanups.push(() => new Promise((r) => server.close(r)));
  return { port };
}

describe('GET /__serve-root (scripts/serve-static.mjs)', () => {
  it('reports the root the server is actually serving', async () => {
    const other = scratchTree();
    const { res } = await startServeStatic(other);
    expect(res.status).toBe('match');
    expect(normalizeRoot(res.reportedRoot)).toBe(normalizeRoot(other));
  }, 20_000);

  it('stays inert for normal requests and never lists a directory', async () => {
    const other = scratchTree();
    const { port } = await startServeStatic(other);
    const page = await fetch(`http://127.0.0.1:${port}/index.html`);
    expect(page.status).toBe(200);
    expect(await page.text()).toContain('scratch tree');
    // A directory without an index still falls back to the root index.html —
    // never a file listing, before or after the endpoint was added.
    const dir = await fetch(`http://127.0.0.1:${port}/no-such-dir`);
    expect(dir.status).toBe(200);
    expect(await dir.text()).toContain('scratch tree');
  }, 20_000);
});

describe('assertServeRoot (the guard Playwright runs)', () => {
  it('REJECTS a server on a scratch port rooted at a different tree', async () => {
    const other = scratchTree();
    const { port } = await startServeStatic(other);
    await expect(assertServeRoot({ port, expectedRoot: REPO_ROOT }))
      .rejects.toThrow(/not this checkout/);
    // The message must carry everything needed to act on it, per the repo's
    // loud-error policy: port, both roots, and how to find the offender.
    const err = await assertServeRoot({ port, expectedRoot: REPO_ROOT }).catch((e) => e);
    const msg = String(err.message);
    expect(msg).toContain(String(port));
    // The reported root verbatim — what the offending process was actually
    // handed — not a realpath'd rewrite of it.
    expect(msg).toContain(other);
    expect(msg).toContain(normalizeRoot(REPO_ROOT));
    expect(msg).toContain(`lsof -nP -iTCP:${port} -sTCP:LISTEN`);
  }, 20_000);

  it('ACCEPTS a server rooted at the current tree', async () => {
    const { port } = await startServeStatic(REPO_ROOT);
    await expect(assertServeRoot({ port, expectedRoot: REPO_ROOT }))
      .resolves.toMatchObject({ status: 'match' });
  }, 20_000);

  it('treats a listener WITHOUT the endpoint as a mismatch, not a pass', async () => {
    // An older serve-static.mjs, or any foreign server on the port: it
    // cannot prove which tree it serves, so it must not be trusted.
    const { port } = await startForeignServer((_req, res) => {
      res.writeHead(404);
      res.end('no such endpoint');
    });
    await expect(probeServeRoot(port, { expectedRoot: REPO_ROOT }))
      .resolves.toMatchObject({ status: 'no-endpoint' });
    await expect(assertServeRoot({ port, expectedRoot: REPO_ROOT }))
      .rejects.toThrow(/did not identify its root/);
  });

  it('treats a 200-with-HTML listener as no-endpoint (identity must be JSON with a root)', async () => {
    const { port } = await startForeignServer((_req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end('<html><body>some other app entirely</body></html>');
    });
    await expect(probeServeRoot(port, { expectedRoot: REPO_ROOT }))
      .resolves.toMatchObject({ status: 'no-endpoint' });
  });

  it('passes when nothing is listening — Playwright will start its own server', async () => {
    const port = await freePort();
    await expect(assertServeRoot({ port, expectedRoot: REPO_ROOT }))
      .resolves.toMatchObject({ status: 'absent' });
  });

  it('is not fooled by a symlinked or trailing-separator spelling of this tree', async () => {
    // macOS mkdtemp hands out /var/... while realpath says /private/var/...;
    // a run whose expected root arrived through any symlink must still match.
    const linkDir = mkdtempSync(join(tmpdir(), 'dubis-serve-root-link-'));
    cleanups.push(() => rmSync(linkDir, { recursive: true, force: true }));
    const link = join(linkDir, 'checkout');
    linkTo(REPO_ROOT, link);
    // The link must genuinely be another spelling of this tree, or the check
    // below would prove nothing (and would fail loudly rather than vacuously).
    expect(realpathSync(link)).toBe(realpathSync(REPO_ROOT));
    const { port } = await startServeStatic(REPO_ROOT);
    await expect(assertServeRoot({ port, expectedRoot: `${link}/` }))
      .resolves.toMatchObject({ status: 'match' });
  }, 20_000);
});

describe('wiring', () => {
  it('the guard runs as a Playwright global setup', async () => {
    const config = await readFile(join(REPO_ROOT, 'playwright.config.mjs'), 'utf8');
    expect(config).toContain('serve-root-setup.mjs');
    // And the setup itself refuses to trust a missing identity endpoint.
    const setup = await readFile(join(REPO_ROOT, 'tests', 'js', 'e2e', 'serve-root-setup.mjs'), 'utf8');
    expect(setup).toContain('assertServeRoot');
  });

  it('resolveServePort matches the config default and honours SERVE_PORT', () => {
    expect(resolveServePort({})).toBe(3123);
    expect(resolveServePort({ SERVE_PORT: '3999' })).toBe(3999);
    expect(() => resolveServePort({ SERVE_PORT: 'not-a-port' })).toThrow(RangeError);
    expect(() => resolveServePort({ SERVE_PORT: '99999' })).toThrow(RangeError);
  });

  it('the endpoint path is identical on both sides of the seam', async () => {
    const server = await readFile(SERVE_STATIC, 'utf8');
    expect(server).toContain(`const IDENTITY_PATH = '${SERVE_ROOT_ENDPOINT}'`);
  });
});
