// tests/js/e2e/serve-root-setup.mjs
//
// Playwright globalSetup: refuse to run E2E against a static server that is
// not rooted at this checkout. By the time globalSetup runs, config.webServer
// has already resolved the port — either by reusing the listener that was
// there (reuseExistingServer: !CI) or by starting this tree's own
// serve-static.mjs. Asking the listener what it serves therefore catches
// every reuse, and passes trivially on a fresh start.
//
// Why: see the header of scripts/serve-root-lib.mjs — an orphaned
// serve-static from another worktree once made a local run measure the wrong
// tree and report a real feature as missing.

import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { assertServeRoot, resolveServePort } from '../../../scripts/serve-root-lib.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..', '..');

export default async function serveRootGuardSetup() {
  // CI always starts its own server (reuseExistingServer is false there),
  // rooted at this tree — nothing to verify, so CI is unaffected.
  if (process.env.CI) return;
  await assertServeRoot({ port: resolveServePort(), expectedRoot: REPO_ROOT });
}
