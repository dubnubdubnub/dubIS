import { defineConfig } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const servePort = parseInt(process.env.SERVE_PORT || '3123', 10);

export default defineConfig({
  testDir: 'tests/js/e2e',
  timeout: 30_000,
  // Absorb transient timing flakes on the self-hosted GUI runners without masking
  // hard failures locally. Local runs get no retries so flakes stay visible.
  retries: process.env.CI ? 2 : 0,
  // Bounded auto-retrying assertion timeout (Playwright has no default expect timeout).
  expect: { timeout: 5000 },
  globalSetup: join(__dirname, 'tests/js/e2e/live/global-setup.mjs'),
  globalTeardown: join(__dirname, 'tests/js/e2e/live/global-teardown.mjs'),
  use: {
    browserName: 'chromium',
    baseURL: `http://localhost:${servePort}`,
    // 'on' forces a screenshot for every test, adding I/O latency on the GUI runners
    // that widens timing windows. Only capture on failure.
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'functional',
      testIgnore: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs', 'live/**'],
    },
    {
      name: 'quality',
      testMatch: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs'],
    },
    {
      name: 'live',
      testDir: 'tests/js/e2e/live',
      testMatch: ['**/*.spec.mjs'],
      timeout: 45_000,
      // The live backend (tests/e2e-server.py) is a single-threaded HTTPServer with
      // module-global mutable state reset via POST /api/_reset in beforeEach. Pin this
      // project to a single, non-parallel worker so isolation no longer depends on CI
      // remembering to pass --workers 1 on the command line.
      workers: 1,
      fullyParallel: false,
    },
  ],
  webServer: {
    command: `node scripts/serve-static.mjs . ${servePort}`,
    port: servePort,
    reuseExistingServer: !process.env.CI,
    timeout: 10_000,
  },
});
